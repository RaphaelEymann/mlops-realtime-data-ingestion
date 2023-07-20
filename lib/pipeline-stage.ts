import { Construct } from "constructs";
import { Stage, StageProps } from 'aws-cdk-lib';
import { RealtimeDataIngestionStack } from './ingestion/data-ingestion-stack';
import { SagemakerCleanupStack } from './sagemaker-cleanup/cleanup-stack';
import { SagemakerStack } from './sagemaker/sagemaker-stack';

export interface RealtimeDataIngestionStageProps extends StageProps {
  readonly prefix: string;
  readonly uniqueSuffix: string;
}

export class RealtimeDataIngestionStage extends Stage {
    
  constructor(scope: Construct, id: string, props: RealtimeDataIngestionStageProps) {
    super(scope, id, props);

    // Stack to deploy the Realtime Data Ingestion 
    const ingestionStack = new RealtimeDataIngestionStack(this, "IngestionStack", {
      prefix: props.prefix,
      s3Suffix: props.uniqueSuffix,
    });   
    
    // Stack to Provision a StepFunction to Wait and delete the SageMaker Studio Domain
    const sagemakerCleanupStack = new SagemakerCleanupStack(this, "SagemakerCleanupStack", {
      prefix: props.prefix,
    });

    // Stack to deploy SageMaker
    new SagemakerStack(this, "SagemakerStack", {
      prefix: props.prefix,
      s3Suffix: props.uniqueSuffix,
      dataBucketArn: ingestionStack.dataBucketArn,
      vpc: ingestionStack.vpc,
      ingestionFirehoseStreamArn: ingestionStack.firehoseStreamArn,
      sagemakerCleanupStateMachineArn: sagemakerCleanupStack.stateMachineArn,
    });    
  }
}
