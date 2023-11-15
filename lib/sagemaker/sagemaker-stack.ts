import { Stack, StackProps, RemovalPolicy } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { PolicyStatement, Effect, Policy, PolicyDocument } from 'aws-cdk-lib/aws-iam';
import { IVpc, SubnetType } from 'aws-cdk-lib/aws-ec2';
import { RDISagemakerStudio } from './sagemaker-domain';
import { RDIFeatureStore } from './feature-store';
import { RDISagemakerProject } from './sagemaker-project';
import { Runtime } from 'aws-cdk-lib/aws-lambda';
import { Bucket, IBucket, BlockPublicAccess, BucketEncryption } from 'aws-cdk-lib/aws-s3';
import { StringParameter } from 'aws-cdk-lib/aws-ssm';

export interface SagemakerStackProps extends StackProps {
  readonly prefix: string;
  readonly s3Suffix: string;
  readonly removalPolicy: RemovalPolicy;
  readonly runtime: Runtime;
  readonly vpc: IVpc;
}
  
export class SagemakerStack extends Stack {
  public readonly prefix: string;
  public readonly s3Suffix: string;
  public readonly removalPolicy: RemovalPolicy;
  public readonly runtime: Runtime;
  public readonly domain: RDISagemakerStudio;
  public readonly featureStore: RDIFeatureStore;
  public readonly project: RDISagemakerProject;
  public readonly experimentBucket: IBucket;

  constructor(scope: Construct, id: string, props: SagemakerStackProps) {
    super(scope, id, props);

    this.prefix = props.prefix;
    this.s3Suffix = props.s3Suffix;
    this.runtime = props.runtime;
    this.removalPolicy = props.removalPolicy || RemovalPolicy.DESTROY;

    // Get the necessary information of the ingestion stack from SSM parameters
    const customResourceLayerArn = StringParameter.fromStringParameterAttributes(this, 'CustomResourceLayerArn', {
      parameterName: `/${props.prefix}/stack-parameters/custom-resource-layer-arn`,
    }).stringValue

    const ingestionFirehoseStreamArn = StringParameter.fromStringParameterAttributes(this, 'FirehoseStreamSSMParameter', {
      parameterName: `/${props.prefix}/stack-parameters/ingestion-firehose-stream-arn`,
    }).stringValue

    const dataBucketArn = StringParameter.fromStringParameterAttributes(this, 'DataBucketSSMParameter', {
      parameterName: `/${props.prefix}/stack-parameters/ingestion-data-bucket-arn`,
    }).stringValue

    // S3 bucket to store the Model artifacts
    this.experimentBucket = new Bucket(this, 'ExperimentBucket', {
      bucketName: `${this.prefix}-sagemaker-experiment-${this.s3Suffix}`,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      encryption: BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: false,
      removalPolicy: this.removalPolicy,
      autoDeleteObjects: this.removalPolicy === RemovalPolicy.DESTROY,
    });

    // Create IAM policy to access the buckets
    const dataAccessDocument = new PolicyDocument({
      statements: [
        new PolicyStatement({
          sid: 'InputAndExperimentBucketsAccess',
          actions: [
            's3:ListBucket',
            's3:ListAllMyBuckets',
            's3:GetBucket*',
            's3:GetObject*', 
            's3:PutObject*', 
            's3:DeleteObject*', 
          ],
          effect: Effect.ALLOW,
          resources: [
            dataBucketArn,
            `${dataBucketArn}/*`,
            this.experimentBucket.bucketArn,
            `${this.experimentBucket.bucketArn}/*`,
          ],
        }),
        new PolicyStatement({
          sid: 'GlueAccess',
          effect: Effect.ALLOW,
          actions: [
            'glue:GetDatabase*',
            'glue:GetTable*',
            'glue:GetPartition*', 
            'glue:SearchTables',
          ],
          resources: [
            'arn:aws:glue:*:*:catalog',
            'arn:aws:glue:*:*:database/sagemaker_featurestore',
            'arn:aws:glue:*:*:table/sagemaker_featurestore/*',
            `arn:aws:glue:*:*:table/${this.prefix}*`,
            `arn:aws:glue:*:*:tableVersion/${this.prefix}*`,
          ],
        }),
        new PolicyStatement({
          sid: 'AthenaAccess',
          effect: Effect.ALLOW,
          actions: [
            'athena:ListDataCatalogs',
            'athena:ListDatabases',
            'athena:ListTableMetadata',
            'athena:GetQueryExecution',
            'athena:GetQueryResults',
            'athena:StartQueryExecution',
            'athena:StopQueryExecution',
          ],
          resources: ['*'],
        }),
      ],
    });
    const dataAccessPolicy = new Policy(this, 'DataPolicy', {
      policyName: `${this.prefix}-data-access-policy`,
      document: dataAccessDocument,
    });

    this.domain = new RDISagemakerStudio(this, 'sagemakerStudio', {
      prefix: this.prefix,
      removalPolicy: this.removalPolicy,
      runtime: this.runtime,
      dataBucketArn: dataBucketArn,
      experimentBucketArn: this.experimentBucket.bucketArn,
      dataAccessPolicy: dataAccessPolicy,
      vpcId: props.vpc.vpcId,
      subnetIds: props.vpc.selectSubnets({ subnetType: SubnetType.PUBLIC }).subnetIds,
      customResourceLayerArn: customResourceLayerArn,
    });

    this.featureStore = new RDIFeatureStore(this, 'featureStore', {
      prefix: this.prefix,
      removalPolicy: this.removalPolicy,
      runtime: this.runtime,
      customResourceLayerArn: customResourceLayerArn,
      firehoseStreamArn: ingestionFirehoseStreamArn,
      s3Suffix: this.s3Suffix,
      dataAccessPolicy: dataAccessPolicy,
    });

    this.project = new RDISagemakerProject(this, 'sagemakerProject', {
      prefix: this.prefix,
      removalPolicy: this.removalPolicy,
      runtime: this.runtime,
      customResourceLayerArn: customResourceLayerArn,
      portfolioId: this.domain.portfolioId,
      domainExecutionRole: this.domain.executionRole,
      dataAccessPolicy: dataAccessPolicy,
    });
  }
}