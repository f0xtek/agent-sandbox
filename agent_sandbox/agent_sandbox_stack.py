from pathlib import Path
from typing import cast

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3_assets as s3_assets
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parent.parent


class AgentSandboxStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        tool_env_asset = s3_assets.Asset(
            self,
            "ttolEnvironmentAsset",
            path=str(REPO_ROOT / "src" / "tool-environment"),
        )

        # confused deputy guard
        micro_vm_trust_principal = cast(
            iam.IPrincipal,
            iam.ServicePrincipal("lambda.amazonaws.com").with_conditions(
                {
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:lambda:{self.region}:{self.account}:microvm-image:*"
                    },
                }
            ),
        )

        build_role = iam.Role(
            self, "MicrovmBuildRole", assumed_by=micro_vm_trust_principal
        )
        tool_env_asset.grant_read(build_role)
        build_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda-microvms/*"
                ],
            )
        )

        execution_role = iam.Role(
            self, "MicrovmExecutionRole", assumed_by=micro_vm_trust_principal
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda-microvms/*"
                ],
            )
        )

        # AWS periodically rotates managed base image versions, get the latest
        # with `aws lambda-microvms list-managed-microvm-images`
        base_image_arn = f"arn:aws:lambda:{self.region}:aws:microvm-image:al2023-1"

        micro_vm_image = _lambda.CfnMicrovmImage(
            self,
            "ToolEnvironmentImage",
            name="agent-sandbox-tool-environment",
            description="Sandboxed Python execution environment for agent tool calls.",
            base_image_arn=base_image_arn,
            base_image_version="1.0",
            build_role_arn=build_role.role_arn,
            cpu_configurations=[{"architecture": "ARM_64"}],
            resources=[{"minimumMemoryInMiB": 512}],
            additional_os_capabilities=[],
            egress_network_connectors=[],
            environment_variables=[],
            hooks={
                "port": 9000,
                "microvmImageHooks": {"ready": "ENABLED", "readyTimeoutInSeconds": 60},
                "microvmHooks": {
                    "run": "ENABLED",
                    "runTimeoutInSeconds": 2,
                    "resume": "ENABLED",
                    "resumeTimeoutInSeconds": 2,
                    "suspend": "ENABLED",
                    "suspendTimeoutInSeconds": 5,
                    "terminate": "ENABLED",
                    "terminateTimeoutInSeconds": 5,
                },
            },
            logging={"cloudWatch": {"logGroup": "/aws/lambda-microvms/agent-sandbox"}},
            code_artifact={"uri": tool_env_asset.s3_object_url},
        )

        session_table = ddb.TableV2(
            self,
            "SandboxSessionTable",
            partition_key={"name": "sessionToken", "type": ddb.AttributeType.STRING},
            time_to_live_attribute="expiresAt",
            removal_policy=RemovalPolicy.DESTROY,
        )

        launcher_lambda_role = iam.Role(
            self,
            "LauncherLambdaRole",
            assumed_by=cast(
                iam.IPrincipal, iam.ServicePrincipal("lambda.amazonaws.com")
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        launcher_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:RunMicrovm", "lambda:CreateMicrovmAuthToken"],
                # lambda-microvms doesn't support resource-level scoping for these
                # actions so tightening further isn't possible.
                resources=["*"],
            )
        )
        launcher_lambda_role.add_to_policy(
            iam.PolicyStatement(
                # Required for every RunMicrovm call, even with no custom connector
                # a MicroVM always passes its default HTTP_INGRESS/INTERNET_EGRESS
                # connectors at run time.
                actions=["lambda:PassNetworkConnector"],
                resources=[
                    f"arn:aws:lambda:{self.region}:aws:network-connector:aws-network-connector:*"
                ],
            )
        )

        session_table.grant_read_write_data(launcher_lambda_role)

        launcher_lambda = _lambda.Function(
            self,
            "LauncherLambdaFunction",
            runtime=_lambda.Runtime.PYTHON_3_14,
            code=_lambda.Code.from_asset(str(REPO_ROOT / "src" / "launcher")),
            handler="handler",
            role=cast(iam.IRole, launcher_lambda_role),
            environment={
                "MICROVM_IMAGE_ARN": micro_vm_image.attr_image_arn,
                "MICROVM_IMAGE_VERSION": micro_vm_image.attr_latest_active_image_version,
                "MICROVM_EXECUTION_ROLE_ARN": execution_role.role_arn,
                "SESSION_TABLE_NAME": session_table.table_name,
            },
            timeout=Duration.seconds(30),
        )

        api = apigw.RestApi(
            self,
            "AgentSandboxApi",
            rest_api_name="Agent Sandbox Execution Gateway",
            description="Ingress endpoint for external agent loops executing safe tool calls.",
            deploy_options={
                "stage_name": "prod",
                "logging_level": apigw.MethodLoggingLevel.ERROR,
                # would otherwise log full request/response bodies (submitted code included)
                "data_trace_enabled": False,
            },
        )

        execute_resource = api.root.add_resource("execute")
        python_execute_resource = execute_resource.add_resource("python")
        lambda_integration = apigw.LambdaIntegration(
            cast(_lambda.IFunction, launcher_lambda)
        )
        python_execute_resource.add_method(
            "POST", lambda_integration, authorization_type=apigw.AuthorizationType.IAM
        )

        CfnOutput(
            self,
            "ApiEndpointUrl",
            value=f"{api.url}execute/python",
            description="The endpoint your external AI Agent platform hits to run sandboxed code.",
        )

        CfnOutput(
            self,
            "MicrovmImageArn",
            value=micro_vm_image.attr_image_arn,
            description="The Lambda MicroVM image ARN backing the sandbox.",
        )
