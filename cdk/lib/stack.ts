import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';

// A deliberately plain Harness: one small model, no tools, managed Memory off. CfnHarness has no observability
// property, so nothing here configures tracing. What the demo shows is what the account setup decides.
export class HarnessStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Execution role, following the sample in the AgentCore harness security docs (public network, trimmed to what a
    // no-tools harness needs: model, ECR Public image pull, X-Ray, logs, metrics, workload identity).
    const role = new iam.Role(this, 'ExecutionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
    });
    const region = this.region, account = this.account;
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['arn:aws:bedrock:*::foundation-model/*', `arn:aws:bedrock:${region}:${account}:*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['ecr-public:GetAuthorizationToken', 'sts:GetServiceBearerToken'], resources: ['*'] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['xray:PutTraceSegments', 'xray:PutTelemetryRecords', 'xray:GetSamplingRules', 'xray:GetSamplingTargets'],
      resources: ['*'],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:DescribeLogStreams'],
      resources: [`arn:aws:logs:${region}:${account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:DescribeLogGroups'], resources: [`arn:aws:logs:${region}:${account}:log-group:*`] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${region}:${account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:PutResourcePolicy'], resources: ['*'] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'], resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' } },
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:GetWorkloadAccessToken', 'bedrock-agentcore:GetWorkloadAccessTokenForJWT'],
      resources: [
        `arn:aws:bedrock-agentcore:${region}:${account}:workload-identity-directory/default`,
        `arn:aws:bedrock-agentcore:${region}:${account}:workload-identity-directory/default/workload-identity/harness_spansdemo-*`,
      ],
    }));

    const harness = new agentcore.CfnHarness(this, 'Harness', {
      harnessName: 'spansdemo',
      executionRoleArn: role.roleArn,
      model: { bedrockModelConfig: { modelId: 'us.anthropic.claude-haiku-4-5-20251001-v1:0', maxTokens: 512 } },
      systemPrompt: [{ text: 'You are a terse assistant. Answer in one short sentence.' }],
      memory: { disabled: {} },      // managed Memory is on by default and billed; this demo does not need it
      maxIterations: 3,
      timeoutSeconds: 120,
    });
    harness.node.addDependency(role);

    new cdk.CfnOutput(this, 'Region', { value: region });
    new cdk.CfnOutput(this, 'HarnessRef', { value: harness.ref });
    new cdk.CfnOutput(this, 'HarnessId', { value: harness.attrHarnessId });
  }
}
