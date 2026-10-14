import json
import os
import time
import urllib.error
import urllib.request
import uuid

import boto3

microvm_client = boto3.client("lambda-microvms")
session_table = boto3.resource("dynamodb").Table(os.environ["SESSION_TABLE_NAME"])

MICROVM_IMAGE_ARN = os.environ["MICROVM_IMAGE_ARN"]
MICROVM_IMAGE_VERSION = os.environ["MICROVM_IMAGE_VERSION"]
MICROVM_EXECUTION_ROLE_ARN = os.environ["MICROVM_EXECUTION_ROLE_ARN"]
MICROVM_EGRESS_NETWORK_CONNECTOR_ARN = os.environ[
    "MICROVM_EGRESS_NETWORK_CONNECTOR_ARN"
]
APP_PORT = 8080
# Must not exceed the MicroVM's own maximumDurationInSeconds (set below).
SESSION_TTL_SECONDS = 28800


def get_or_create_session(session_token: str | None) -> dict:
    if session_token:
        item = session_table.get_item(Key={"sessionToken": session_token}).get("Item")
        if item:
            return item

    run_response = microvm_client.run_microvm(
        imageIdentifier=MICROVM_IMAGE_ARN,
        imageVersion=MICROVM_IMAGE_VERSION,
        executionRoleArn=MICROVM_EXECUTION_ROLE_ARN,
        egressNetworkConnectors=[MICROVM_EGRESS_NETWORK_CONNECTOR_ARN],
        maximumDurationInSeconds=SESSION_TTL_SECONDS,
        idlePolicy={
            "maxIdleDurationSeconds": 60,
            "suspendedDurationSeconds": SESSION_TTL_SECONDS,
            "autoResumeEnabled": True,
        },
    )

    session = {
        "sessionToken": str(uuid.uuid4()),
        "microvmId": run_response["microvmId"],
        "endpoint": run_response["endpoint"],
    }

    session_table.put_item(
        Item={**session, "expiresAt": int(time.time()) + SESSION_TTL_SECONDS}
    )

    return session


def handler(event, context):
    try:
        request_body = json.loads(event["body"]) if event.get("body") else {}
        code_to_execute = request_body.get("code")

        if not code_to_execute:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {"error": "Bad Request: 'code' string field is required."}
                ),
            }

        session = get_or_create_session(request_body.get("sessionToken"))
        print(f"[Launcher] Routing execution to MicroVM {session['microvmId']}")

        # AWS rejects all unauthenticated traffic
        token_response = microvm_client.create_microvm_auth_token(
            microvmIdentifier=session["microvmId"],
            expirationInMinutes=5,
            allowedPorts=[{"port": APP_PORT}],
        )
        auth_token = token_response["authToken"]["X-aws-proxy-auth"]

        target_url = f"https://{session['endpoint']}/execute/python"
        request = urllib.request.Request(
            target_url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-aws-proxy-auth": auth_token,
                "X-aws-proxy-port": str(APP_PORT),
            },
            data=json.dumps(
                {
                    "code": code_to_execute,
                    "timeout": request_body.get("timeout") or 30,
                }
            ).encode("utf-8"),
        )

        try:
            with urllib.request.urlopen(request) as response:
                is_success = 200 <= response.status < 300
                execution_result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as response_error:
            is_success = False
            execution_result = json.loads(response_error.read().decode("utf-8"))

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "sessionToken": session["sessionToken"],
                    "status": "success" if is_success else "failed",
                    "data": execution_result,
                }
            ),
        }
    except Exception as error:
        print(f"[Launcher Error]: {error}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"error": "Internal Sandbox Error", "details": str(error)}
            ),
        }
