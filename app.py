#!/usr/bin/env python3

import aws_cdk as cdk

from agent_sandbox.agent_sandbox_stack import AgentSandboxStack

app = cdk.App()

AgentSandboxStack(app, "AgentSandboxStack")

app.synth()
