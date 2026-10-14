from dataclasses import dataclass
from typing import cast

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from constructs import Construct


@dataclass
class NetworkStackOutputs:
    network_connector_arn: str


class AgentSandboxNetworkStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # MicroVMs attached via the connector below have no path to the public internet.
        vpc = ec2.Vpc(
            self,
            "SandboxEgressVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                )
            ],
        )

        connector_security_group = ec2.SecurityGroup(
            self,
            "SandboxEgressConnectorSecurityGroup",
            vpc=vpc,
            description="Attached to the sandbox egress network connector.",
            allow_all_outbound=False,
        )

        # Lambda assumes this to manage the ENIs it creates for the connector
        # in these subnets (ec2:CreateNetworkInterface and related
        # describe/delete operations)
        operator_role = iam.Role(
            self,
            "SandboxEgressConnectorOperatorRole",
            assumed_by=cast(
                iam.IPrincipal, iam.ServicePrincipal("lambda.amazonaws.com")
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                )
            ],
        )

        network_connector = _lambda.CfnNetworkConnector(
            self,
            "SandboxEgressNetworkConnector",
            name="agent-sandbox-egress",
            operator_role=operator_role.role_arn,
            configuration=_lambda.CfnNetworkConnector.ConfigProperty(
                vpc_egress_configuration=_lambda.CfnNetworkConnector.VpcEgressConfigurationProperty(
                    associated_compute_resource_types=["MicroVm"],
                    network_protocol="IPv4",
                    security_group_ids=[connector_security_group.security_group_id],
                    subnet_ids=vpc.select_subnets(
                        subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                    ).subnet_ids,
                )
            ),
        )

        CfnOutput(
            self,
            "NetworkConnectorArn",
            value=network_connector.attr_arn,
            description="VPC-only egress network connector. Denies MicroVMs internet access.",
        )

        self.outputs = NetworkStackOutputs(
            network_connector_arn=network_connector.attr_arn
        )
