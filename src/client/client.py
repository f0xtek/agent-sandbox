import json
import os

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

API_ENDPOINT = os.getenv(
    "API_ENDPOINT"
)  # e.g. https://{api-id}.execute-api.{region}.amazonaws.com/prod/execute/python
AWS_REGION = os.getenv("AWS_REGION", "eu-west-1")


def run_code_via_launcher(
    endpoint: str, region: str, code_string: str, session_token: str | None = None
) -> dict:
    payload = {"code": code_string, "timeout": 30}
    if session_token:
        payload["sessionToken"] = session_token

    request = AWSRequest(method="POST", url=endpoint, data=json.dumps(payload))
    SigV4Auth(boto3.Session().get_credentials(), "execute-api", region).add_auth(
        request
    )

    response = requests.post(endpoint, data=request.body, headers=dict(request.headers))
    return response.json()


if __name__ == "__main__":
    if API_ENDPOINT is None:
        print("Missing API_ENDPOINT environment variable.")
    else:
        result = run_code_via_launcher(
            API_ENDPOINT, AWS_REGION, "import math; print(math.factorial(5))"
        )
        print(result)
