"""Example workflow pipeline script for the blockchain forecasting pipeline.
                                               . -ModelStep
                                              .
    Process-> DataQualityCheck/DataBiasCheck -> Train -> Evaluate -> Condition .
                |                              .
                |                                . -(stop)
                |
                 -> CreateModel
                         |
                         |
                          -> BatchTransform -> ModelQualityCheck

Implements a get_pipeline(**kwargs) method.
Example of DeepAR pipeline: https://github.com/aws-samples/amazon-sagemaker-forecasting-air-pollution-with-deepar/blob/main/02_manual_ml_pipeline_creation_for_air_quality_forecasting.ipynb
"""
import os
import json

import boto3
import sagemaker
import sagemaker.session

from sagemaker.estimator import Estimator
from sagemaker.inputs import TrainingInput, TransformInput
from sagemaker.model import Model
from sagemaker.transformer import Transformer

from sagemaker.model_metrics import (
    MetricsSource,
    ModelMetrics,
    FileSource
)
from sagemaker.drift_check_baselines import DriftCheckBaselines
from sagemaker.processing import (
    ProcessingInput,
    ProcessingOutput,
    ScriptProcessor
)
from sagemaker.sklearn.processing import SKLearnProcessor
from sagemaker.workflow.conditions import ConditionLessThanOrEqualTo
from sagemaker.workflow.condition_step import ConditionStep
from sagemaker.workflow.functions import JsonGet
from sagemaker.workflow.parameters import (
    ParameterBoolean,
    ParameterInteger,
    ParameterString
)
from sagemaker.workflow.pipeline import Pipeline
from sagemaker.workflow.properties import PropertyFile
from sagemaker.workflow.steps import (
    ProcessingStep,
    TrainingStep,
    TransformStep
)
from sagemaker.workflow.check_job_config import CheckJobConfig
from sagemaker.workflow.execution_variables import ExecutionVariables
from sagemaker.workflow.functions import Join
from sagemaker.model_monitor import DatasetFormat
from sagemaker.workflow.quality_check_step import (
    ModelQualityCheckConfig,
    QualityCheckStep
)
from sagemaker.workflow.model_step import ModelStep
from sagemaker.model import Model
from sagemaker.workflow.pipeline_context import PipelineSession


#
# Constants
#
# Directories
BASE_DIR = os.path.dirname(os.path.realpath(__file__))
PROCESSING_FOLDER_PREFIX = "/opt/ml/processing"
LOCAL_DATA_DIR = f"{PROCESSING_FOLDER_PREFIX}/data"
LOCAL_MODEL_DIR =  f"{PROCESSING_FOLDER_PREFIX}/model"
LOCAL_TEST_DIR = f"{LOCAL_DATA_DIR}/test"
LOCAL_TRANSFORM_DIR = f"{LOCAL_DATA_DIR}/transform"
LOCAL_EVALUATION_DIR = f"{LOCAL_DATA_DIR}/evaluation"
# Resources names
#MODEL_PACKAGE_GROUP_NAME = f"PackageGroup"
PROCESSING_STEP_NAME = f"PreprocessData"
PROCESSING_JOB_NAME = f"PreprocessingJob"
TRAINING_JOB_NAME = f"TrainingJob"
TRAINING_STEP_NAME = f"TrainModel"
CREATE_MODEL_STEP_NAME = f"CreateModel"
TRANSFORM_STEP_NAME = f"Transform"
TRANSFORM_JOB_NAME = f"TransformOutputsProcessingJob"
PROCESSING_TRANSFORM_STEP_NAME = f"ProcessingTransformOutputs"
MODEL_QUALITY_CHECK_STEP_NAME = f"QualityCheck"
EVALUATION_JOB_NAME = f"EvaluationJob"
EVALUATION_REPORT_NAME = f"EvaluationReport"
EVALUATION_STEP_NAME = f"EvaluateModel"
REGISTER_MODEL_STEP_NAME = f"RegisterModel"
CONDITON_STEP_NAME = f"CheckMQLCondition"
FAIL_STEP_NAME = f"Fail"
# Instances
BASE_INSTANCE_TYPE = "ml.m5.large"
PROCESSING_INSTANCE_TYPE = "ml.t3.medium"
TRAINING_INSTANCE_TYPE = "ml.c5.2xlarge"
CREATE_MODEL_INSTANCE_TYPE = BASE_INSTANCE_TYPE
TRANSFORM_INSTANCE_TYPE = BASE_INSTANCE_TYPE
CHECK_INSTANCE_TYPE = BASE_INSTANCE_TYPE
INFERENCE_INSTANCES_TYPE = ["ml.t2.medium", BASE_INSTANCE_TYPE]
ACCELERATOR_TYPE = "ml.eia1.medium"

def get_sagemaker_client(region):
     """Gets the sagemaker client.

        Args:
            region: the aws region to start the session
            default_bucket: the bucket to use for storing the artifacts

        Returns:
            `sagemaker.session.Session instance
        """
     boto_session = boto3.Session(region_name=region)
     sagemaker_client = boto_session.client("sagemaker")
     return sagemaker_client


def get_session(region, default_bucket):
    """Gets the sagemaker session based on the region.

    Args:
        region: the aws region to start the session
        default_bucket: the bucket to use for storing the artifacts

    Returns:
        `sagemaker.session.Session instance
    """

    boto_session = boto3.Session(region_name=region)

    sagemaker_client = boto_session.client("sagemaker")
    runtime_client = boto_session.client("sagemaker-runtime")
    return sagemaker.session.Session(
        boto_session=boto_session,
        sagemaker_client=sagemaker_client,
        sagemaker_runtime_client=runtime_client,
        default_bucket=default_bucket,
    )


def get_pipeline_session(region, default_bucket):
    """Gets the pipeline session based on the region.

    Args:
        region: the aws region to start the session
        default_bucket: the bucket to use for storing the artifacts

    Returns:
        PipelineSession instance
    """

    boto_session = boto3.Session(region_name=region)
    sagemaker_client = boto_session.client("sagemaker")

    return PipelineSession(
        boto_session=boto_session,
        sagemaker_client=sagemaker_client,
        default_bucket=default_bucket,
    )

def get_pipeline_custom_tags(new_tags, region, sagemaker_project_name=None):
    try:
        sm_client = get_sagemaker_client(region)
        response = sm_client.describe_project(ProjectName=sagemaker_project_name)
        sagemaker_project_arn = response["ProjectArn"]
        response = sm_client.list_tags(
            ResourceArn=sagemaker_project_arn)
        project_tags = response["Tags"]
        for project_tag in project_tags:
            new_tags.append(project_tag)
    except Exception as e:
        print(f"Error getting project tags: {e}")
    return new_tags


def get_pipeline(
    region,
    role=None,
    default_bucket=None,
    sagemaker_project_name=None,
    sagemaker_project_id=None,
    model_package_group_name="Blockchain",
):
    """Gets a SageMaker ML Pipeline instance working with on blockchain forecasting data.

    Args:
        region: AWS region to create and run the pipeline.
        role: IAM role to create and run steps and pipeline.
        default_bucket: the bucket to use for storing the artifacts

    Returns:
        an instance of a pipeline
    """
    # Create the base job prefix string based on project name and project id if they are not null
    if sagemaker_project_name is None:
        sagemaker_project_name = "blockchain-forecasting"
    if sagemaker_project_id is None:
        sagemaker_project_id = "mlops-pipeline"
    base_job_prefix = f"{sagemaker_project_name}-{sagemaker_project_id}"
    execution_prefix = ExecutionVariables.PIPELINE_EXECUTION_ID
    sagemaker_session = get_session(region, default_bucket)
    default_bucket = sagemaker_session.default_bucket()
    if role is None:
        role = sagemaker.session.get_execution_role(sagemaker_session)

    pipeline_session = get_pipeline_session(region, default_bucket)
    
    # parameters for pipeline execution
    processing_instance_count = ParameterInteger(
        name="ProcessingInstanceCount", 
        default_value=1
    )
    model_approval_status = ParameterString(
        name="ModelApprovalStatus", 
        default_value="PendingManualApproval"
    )
    # for model quality check step
    skip_check_model_quality = ParameterBoolean(
        name="SkipModelQualityCheck", 
        default_value = False
    )
    register_new_baseline_model_quality = ParameterBoolean(
        name="RegisterNewModelQualityBaseline", 
        default_value=False
    )
    supplied_baseline_statistics_model_quality = ParameterString(
        name="ModelQualitySuppliedStatistics", 
        default_value=''
    )
    supplied_baseline_constraints_model_quality = ParameterString(
        name="ModelQualitySuppliedConstraints", 
        default_value=''
    )

    # Read the Feature Group Name from SageMaker feature store
    ssm_client = boto3.client("ssm")
    feature_group_name = ssm_client.get_parameter(
        Name="/rdi-mlops/stack-parameters/sagemaker-feature-group-name"
    )["Parameter"]["Value"]
    # Read the SSM Paramters for the model prediction target
    model_target_parameters = {}
    try:
        response = ssm_client.get_parameters_by_path(
            Path="/rdi-mlops/sagemaker/model-build/target",
            Recursive=False,
            WithDecryption=False,
        )
        for param in response["Parameters"]:
            model_target_parameters[param["Name"].split("/")[-1]] = param["Value"]
    except Exception as e:
        print(f"An error occurred reading the model target parameters: {e}")

    # Read the SSM Parameters storing the model training hyperparamters
    model_training_hyperparameters = {}
    try:
        response = ssm_client.get_parameters_by_path(
            Path="/rdi-mlops/sagemaker/model-build/training-hyperparameters",
            Recursive=False,
            WithDecryption=False,
        )
        for param in response["Parameters"]:
            model_training_hyperparameters[param["Name"].split("/")[-1]] = param["Value"]
    except Exception as e:
        print(f"An error occurred reading the model training hyperparameters: {e}")

    # Read the SSM Parameters storing the model validation thresholds by the parameters path
    model_validation_thresholds = {}
    try:
        response = ssm_client.get_parameters_by_path(
            Path="/rdi-mlops/sagemaker/model-build/validation-threshold",
            Recursive=False,
            WithDecryption=False,
        )
        for param in response["Parameters"]:
            model_validation_thresholds[param["Name"].split("/")[-1]] = float(param["Value"])
    except Exception as e:
        print(f"An error occurred reading the model validation thresholds: {e}")

    #
    # Step 1: Data Preprocessing
    #
    # processing step for feature engineering
    # List of processing images: https://github.com/aws/sagemaker-python-sdk/tree/master/src/sagemaker/image_uri_config
    # If we need to create our own container: https://docs.aws.amazon.com/sagemaker/latest/dg/processing-container-run-scripts.html
    processing_image_uri = sagemaker.image_uris.get_base_python_image_uri(
        region=region, 
        py_version="310"
    )

    data_preprocessor = ScriptProcessor(
        image_uri=processing_image_uri,
        command=["python3"],
        instance_type=PROCESSING_INSTANCE_TYPE,
        instance_count=1,
        base_job_name=PROCESSING_JOB_NAME,
        sagemaker_session=pipeline_session,
        role=role,
    )

    preprocessing_step_args = data_preprocessor.run(
        outputs=[
            ProcessingOutput(output_name="train", source=f"{LOCAL_DATA_DIR}/train"),
            ProcessingOutput(output_name="validation", source=f"{LOCAL_DATA_DIR}/validation"),
            ProcessingOutput(output_name="test", source=f"{LOCAL_DATA_DIR}/test")
        ],
        code=os.path.join(BASE_DIR, "preprocess.py"),
        arguments=[
            "--region", region,
            "--feature-group-name", feature_group_name,
            "--artifacts-bucket", default_bucket,
            "--base-job-prefix", base_job_prefix,
            "--freq", model_target_parameters["freq"],
            "--target-col", model_target_parameters["target_col"],
            "--prediction-length", model_target_parameters["prediction_length"],
        ],
    )

    step_preprocessing = ProcessingStep(
        name=PROCESSING_STEP_NAME,
        step_args=preprocessing_step_args,        
    )

    #
    # Step 2: Model Training
    #
    model_path = f"s3://{default_bucket}/{base_job_prefix}/model_training/output"
    deepar_image_uri = sagemaker.image_uris.retrieve(
        framework="forecasting-deepar",
        region=region, 
        version="1"
    )

    deepar_estimator = Estimator(
        image_uri=deepar_image_uri,
        sagemaker_session=pipeline_session,
        role=role,
        instance_count=1,
        instance_type=TRAINING_INSTANCE_TYPE,
        base_job_name=TRAINING_JOB_NAME,
        use_spot_instances=True,
        max_run=60*60-1,
        max_wait=60*60,
        output_path=model_path,
    )
    hyperparameters = {
        "time_freq": model_target_parameters["freq"],
        "epochs": model_training_hyperparameters["epochs"],
        "early_stopping_patience": model_training_hyperparameters["early_stopping_patience"],
        "mini_batch_size": model_training_hyperparameters["mini_batch_size"],
        "learning_rate": model_training_hyperparameters["learning_rate"],
        "context_length": model_target_parameters["prediction_length"],
        "prediction_length": model_target_parameters["prediction_length"],
        "likelihood": "negative-binomial"
    }
    deepar_estimator.set_hyperparameters(**hyperparameters)
    preprocessing_step_args = deepar_estimator.fit(
        inputs={
            "train": TrainingInput(
                s3_data=step_preprocessing.properties.ProcessingOutputConfig.Outputs[
                    "train"
                ].S3Output.S3Uri,
                content_type="json",
            ),
            # The DeepAR dataset used to validate the model while training is called test
            # so we pass the validation dataset as test
            "test": TrainingInput(
                s3_data=step_preprocessing.properties.ProcessingOutputConfig.Outputs[
                    "validation"
                ].S3Output.S3Uri,
                content_type="json",
            ),
        },
    )

    step_train = TrainingStep(
        name=TRAINING_STEP_NAME,
        step_args=preprocessing_step_args,
        depends_on=[PROCESSING_STEP_NAME],
    )

    #
    # Step 3: Create the Model
    #
    model = Model(
        image_uri=deepar_image_uri,
        model_data=step_train.properties.ModelArtifacts.S3ModelArtifacts,
        sagemaker_session=pipeline_session,
        role=role,
    )

    preprocessing_step_args = model.create(
        instance_type=CREATE_MODEL_INSTANCE_TYPE,
        accelerator_type=ACCELERATOR_TYPE,
    )

    step_create_model = ModelStep(
        name=CREATE_MODEL_STEP_NAME,
        step_args=preprocessing_step_args,
    )

    #
    # Step 4: Batch Transform
    #
    # Batch Transform for DeepAR
    # documentation: https://docs.aws.amazon.com/sagemaker/latest/dg/deepar-in-formats.html#deepar-batch
    deepar_environment_param = {
        "num_samples": 100,
        "output_types": ["quantiles", "mean"],
        "quantiles": ["0.1", "0.5", "0.9"]
    }
    transform_output_path = f"s3://{default_bucket}/{base_job_prefix}/batch_transform/output"
    transformer = Transformer(
        model_name=step_create_model.properties.ModelName,
        instance_type=TRANSFORM_INSTANCE_TYPE,
        instance_count=1,
        accept="application/jsonlines",
        strategy="SingleRecord",
        assemble_with="Line",
        output_path=transform_output_path,
        sagemaker_session=pipeline_session,
        env={
            "DEEPAR_INFERENCE_CONFIG": json.dumps(deepar_environment_param)
        }
    )

    transform_inputs = TransformInput(
        data=Join(
            on = "/", 
            values=[
                step_preprocessing.properties.ProcessingOutputConfig.Outputs["test"].S3Output.S3Uri,
                "test-inputs.json"
            ])
    )

    # The output of the DeepAR Tranform is also in JSON lines format, with one line per prediction
    # The prediction will have the following format:
    # { "quantiles": { "0.1": [...], "0.5": [...], "0.9": [...]}}
    preprocessing_step_args = transformer.transform(
        data=transform_inputs.data,
        content_type="application/jsonlines",
        split_type="Line",
    )

    step_transform = TransformStep(
        name=TRANSFORM_STEP_NAME,
        step_args=preprocessing_step_args,
    )

    #
    # Step 5: Evaluate the results of the Batch transform
    #
    # Process the Batch Transform Outputs with the target data to have a single CSV file with the 
    # following format:
    # target, mean, quantile1, quantile5, quantile9
    evaluate_processor = ScriptProcessor(
        image_uri=processing_image_uri,
        command=["python3"],
        instance_type=PROCESSING_INSTANCE_TYPE,
        instance_count=1,
        base_job_name=EVALUATION_JOB_NAME,
        sagemaker_session=pipeline_session,
        role=role,
    )

    eval_step_args = evaluate_processor.run(
        inputs=[
            ProcessingInput(
                source=step_preprocessing.properties.ProcessingOutputConfig.Outputs[
                    "test"
                ].S3Output.S3Uri,
                destination=LOCAL_TEST_DIR,
            ),
            ProcessingInput(
                source=step_transform.properties.TransformOutput.S3OutputPath,
                destination=LOCAL_TRANSFORM_DIR,
            ),
        ],
        outputs=[
            ProcessingOutput(output_name="evaluation", source=LOCAL_EVALUATION_DIR)
        ],
        code=os.path.join(BASE_DIR, "evaluate.py"),
        arguments=[
            "--target-col", model_target_parameters["target_col"]
        ],
    )
    
    evaluation_report = PropertyFile(
        name=EVALUATION_REPORT_NAME,
        output_name="evaluation",
        path="evaluation.json",
    )

    step_eval = ProcessingStep(
        name=EVALUATION_STEP_NAME,
        step_args=eval_step_args,
        property_files=[evaluation_report],
        depends_on=[TRANSFORM_STEP_NAME], 
    )

    #
    # Step 6: Check the Model Quality
    #
    # In this `QualityCheckStep` we calculate the baselines for statistics and constraints using the
    # predictions that the model generates from the test dataset (output from the TransformStep). We define
    # the problem type as 'Regression' in the `ModelQualityCheckConfig` along with specifying the columns
    # which represent the input and output. Since the dataset has no headers, `_c0`, `_c1` are auto-generated
    # header names that should be used in the `ModelQualityCheckConfig`.

    check_job_config = CheckJobConfig(
        role=role,
        instance_count=1,
        instance_type=CHECK_INSTANCE_TYPE,
        volume_size_in_gb=120,
        sagemaker_session=pipeline_session,
    )

    model_quality_check_config = ModelQualityCheckConfig(
        baseline_dataset=Join(
            on="/",
            values=[
                step_eval.properties.ProcessingOutputConfig.Outputs["evaluation"].S3Output.S3Uri,
                "targets-quantiles.csv"
            ]),
        dataset_format=DatasetFormat.csv(header=True),
        output_s3_uri=Join(
            on="/", 
            values=[
                "s3:/", 
                default_bucket, 
                base_job_prefix, 
                execution_prefix, 
                "modelqualitycheck"
            ]),
        problem_type='Regression',
        inference_attribute='quantile5',
        ground_truth_attribute='target'
    )

    model_quality_check_step = QualityCheckStep(
        name=MODEL_QUALITY_CHECK_STEP_NAME,
        skip_check=skip_check_model_quality,
        register_new_baseline=register_new_baseline_model_quality,
        quality_check_config=model_quality_check_config,
        check_job_config=check_job_config,
        supplied_baseline_statistics=supplied_baseline_statistics_model_quality,
        supplied_baseline_constraints=supplied_baseline_constraints_model_quality,
        model_package_group_name=model_package_group_name
    )

    model_metrics = ModelMetrics(
        model_statistics=MetricsSource(
            s3_uri=model_quality_check_step.properties.CalculatedBaselineStatistics,
            content_type="application/json",
        ),
        model_constraints=MetricsSource(
            s3_uri=model_quality_check_step.properties.CalculatedBaselineConstraints,
            content_type="application/json",
        )
    )

    drift_check_baselines = DriftCheckBaselines(
        model_statistics=MetricsSource(
            s3_uri=model_quality_check_step.properties.BaselineUsedForDriftCheckStatistics,
            content_type="application/json",
        ),
        model_constraints=MetricsSource(
            s3_uri=model_quality_check_step.properties.BaselineUsedForDriftCheckConstraints,
            content_type="application/json",
        )
    )

    ### Register the model

    # The two parameters in `RegisterModel` that hold the metrics calculated by the `ClarifyCheckStep` and
    # `QualityCheckStep` are `model_metrics` and `drift_check_baselines`.

    # `drift_check_baselines` - these are the baseline files that will be used for drift checks in
    # `QualityCheckStep` or `ClarifyCheckStep` and model monitoring jobs that are set up on endpoints hosting this model.
    # `model_metrics` - these should be the latest baslines calculated in the pipeline run. This can be set
    # using the step property `CalculatedBaseline`

    # The intention behind these parameters is to give users a way to configure the baselines associated with
    # a model so they can be used in drift checks or model monitoring jobs. Each time a pipeline is executed, users can
    # choose to update the `drift_check_baselines` with newly calculated baselines. The `model_metrics` can be used to
    # register the newly calculated baslines or any other metrics associated with the model.

    # Every time a baseline is calculated, it is not necessary that the baselines used for drift checks are updated to
    # the newly calculated baselines. In some cases, users may retain an older version of the baseline file to be used
    # for drift checks and not register new baselines that are calculated in the Pipeline run.

    register_step_args = model.register(
        content_types=["application/json"],
        response_types=["application/json"],
        inference_instances=INFERENCE_INSTANCES_TYPE,
        transform_instances=[TRANSFORM_INSTANCE_TYPE],
        model_package_group_name=model_package_group_name,
        approval_status=model_approval_status,
        model_metrics=model_metrics,
        drift_check_baselines=drift_check_baselines,
    )

    step_register = ModelStep(
        name=REGISTER_MODEL_STEP_NAME,
        step_args=register_step_args,
    )

    # condition step for evaluating model quality and branching execution
    print(f"model validation threshold used is : mean_quantile_loss <= {model_validation_thresholds['mean_quantile_loss']}")
    cond_lte = ConditionLessThanOrEqualTo(
        left=JsonGet(
            step_name=step_eval.name,
            property_file=evaluation_report,
            json_path="deepar_metrics.mean_quantile_loss.value"
        ),
        right=model_validation_thresholds["mean_quantile_loss"],
    )
    step_cond = ConditionStep(
        name=CONDITON_STEP_NAME,
        conditions=[cond_lte],
        if_steps=[step_register],
        else_steps=[],
    )

    # pipeline instance
    pipeline = Pipeline(
        name=base_job_prefix,
        parameters=[
            PROCESSING_INSTANCE_TYPE,
            processing_instance_count,
            TRAINING_INSTANCE_TYPE,
            model_approval_status,
            feature_group_name,

            skip_check_model_quality,
            register_new_baseline_model_quality,
            supplied_baseline_statistics_model_quality,
            supplied_baseline_constraints_model_quality,
        ],
        steps=[
            step_preprocessing, 
            step_train, 
            step_create_model, 
            step_transform, 
            step_eval,
            model_quality_check_step, 
            step_cond
        ],
        sagemaker_session=pipeline_session,
    )
    return pipeline
