import * as cdk from 'aws-cdk-lib';
import { HarnessStack } from '../lib/stack';

const app = new cdk.App();
new HarnessStack(app, 'HarnessSpansEvaluations', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.DEMO_REGION ?? 'us-east-1' },
});
