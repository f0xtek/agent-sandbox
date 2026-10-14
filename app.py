#!/usr/bin/env python3

import aws_cdk as cdk

from agent_sandbox.agent_sandbox_stack import AgentSandboxStack, AgentSandboxStackProps
from agent_sandbox.network_stack import AgentSandboxNetworkStack

app = cdk.App()

network_stack = AgentSandboxNetworkStack(app, "AgentSandboxNetworkStack")

AgentSandboxStack(
    app,
    "AgentSandboxStack",
    props=AgentSandboxStackProps(
        network_connector_arn=network_stack.outputs.network_connector_arn
    ),
)

app.synth()
