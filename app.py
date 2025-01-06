import os
import typing
from urllib.parse import urlparse
from aws_cdk import (
    aws_lambda,
    aws_ecr,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    App, Aws, Duration, Stack,CfnOutput,CfnParameter,Fn,
    aws_iam as iam,
    aws_logs as logs
)
from constructs import Construct

class LambdaContainerFunctionStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)


        image_name    = "react-app"

        ##
        ## If use_pre_existing_image is True
        ## then use an image that already exists in ECR.
        ## Otherwise, build a new image
        ##
        use_pre_existing_image = False



        ##
        ## ECR
        ##
        if (use_pre_existing_image):

            ##
            ## Container was build previously, or elsewhere.
            ## Use the pre-existing container
            ##
            ecr_repository = aws_ecr.Repository.from_repository_attributes(self,
                id              = "ECR",
                repository_arn  ='arn:aws:ecr:{0}:{1}:repository'.format(Aws.REGION, Aws.ACCOUNT_ID),
                repository_name = image_name
            ) ## aws_ecr.Repository.from_repository_attributes
            print (Aws.REGION, Aws.ACCOUNT_ID)
            ##
            ## Container Image.
            ## Pulled from the ECR repository.
            ##
            # ecr_image is expecting a `Code` type, so casting `EcrImageCode` to `Code` resolves mypy error
            ecr_image = typing.cast("aws_lambda.Code", aws_lambda.EcrImageCode(
                repository = ecr_repository
            )) ## aws_lambda.EcrImageCode

        else:
            ##
            ## Create new Container Image.
            ##
            ecr_image = aws_lambda.EcrImageCode.from_asset_image(
                directory = os.path.join(os.getcwd(), "lambda-image")
            )

        ## Lambda Function
        ##
        myfunc = aws_lambda.Function(self,
          id            = "lambdaContainerFunction",
          description   = "Lambda Container Function",
          code          = ecr_image,
          ##
          ## Handler and Runtime must be *FROM_IMAGE*
          ## when provisioning Lambda from Container.
          ##
          handler       = aws_lambda.Handler.FROM_IMAGE,
          runtime       = aws_lambda.Runtime.FROM_IMAGE,
          environment   = {"AWS_LWA_INVOKE_MODE":"RESPONSE_STREAM"},
          function_name = "react-app-function",
          memory_size   = 128,
          reserved_concurrent_executions = 10,
          timeout       = Duration.seconds(120)
        ) 
        # attach policy
        myfunc_role = myfunc.role
        myfunc_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"))
        myfunc_role.attach_inline_policy(iam.Policy(self, "MyInlinePolicy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["bedrock:InvokeModelWithResponseStream"],
                    resources=["*"]
                )
            ]
        ))

        aws_lambda.CfnPermission(self, "MyCfnPermission1",
            action="lambda:InvokeFunctionUrl",
            function_name=myfunc.function_name,
            principal="edgelambda.amazonaws.com",
            function_url_auth_type="AWS_IAM"
        )
        aws_lambda.CfnPermission(self, "MyCfnPermission2",
            action="lambda:InvokeFunctionUrl",
            function_name=myfunc.function_name,
            principal="cloudfront.amazonaws.com",
            function_url_auth_type="AWS_IAM"
        )

        ## aws_lambda.Function
        self.my_function_url = myfunc.add_function_url(
            auth_type = aws_lambda.FunctionUrlAuthType.AWS_IAM,
            invoke_mode = aws_lambda.InvokeMode.RESPONSE_STREAM
        )
        CfnOutput(self, "react-app-function-url", value=self.my_function_url.url)


class EdgelambdaStack(Stack):
    def __init__(self, scope: Construct, id: str,lambda_stack:LambdaContainerFunctionStack, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        domain_name = Fn.select(2, Fn.split("/", lambda_stack.my_function_url.url))
        edge_lambda_role = iam.Role(self, "edge_lambda_role",assumed_by=iam.CompositePrincipal(iam.ServicePrincipal("edgelambda.amazonaws.com"),iam.ServicePrincipal("lambda.amazonaws.com")))

        edge_lambda_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaRole"))
        ## signv4 lambda deployment
        edgelambda = aws_lambda.Function(self, "edgelambda",
            code=aws_lambda.Code.from_asset("lib/lambda-handler/edge_lambda"),
            handler="mini_lambda_handler.lambda_handler",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            role=edge_lambda_role,
            timeout=Duration.seconds(10)
        )
        edge_lambda_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"))
        edge_lambda_role.attach_inline_policy(iam.Policy(self, "invokelambdaurl",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["lambda:InvokeFunctionUrl"],
                    resources=["*"]
                )
            ]
        ))
        ## cloudfront distribution
        react_app_distribution = cloudfront.Distribution(self, "react-app-distribution",
            default_behavior=cloudfront.BehaviorOptions(
                compress=True,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                origin=origins.HttpOrigin(domain_name,custom_headers={"TARGET_ORIGIN":domain_name}),
                edge_lambdas=[cloudfront.EdgeLambda(
                    function_version=edgelambda.current_version,
                    event_type=cloudfront.LambdaEdgeEventType.ORIGIN_REQUEST,
                    include_body=True
                )]
            )
        ) 

        CfnOutput(self, "react-app-distribution-url", value="https://"+react_app_distribution.domain_name)


app = App()
env1 = {'region': 'us-west-2'}
lambda_stack =  LambdaContainerFunctionStack(app, "LambdaContainerFunctionStack", env=env1,cross_region_references=True)
env2 = {'region': 'us-east-1'}
edgelambda_stack = EdgelambdaStack(app, "EdgelambdaStack", lambda_stack=lambda_stack, env=env2,cross_region_references=True)
app.synth()


