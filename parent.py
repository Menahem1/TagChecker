import json
import boto3
import os

def lambda_handler(event, context):
  
    print('INFO! lambda_handler:: Event Received:')
    print(json.dumps(event))
    
    
    # Download the JSON document that list the parameters files
    try:
        param_region = os.environ['RegionName']
        param_bucket = os.environ['Bucket']
        param_key = os.environ['Key']
        print('INFO! lambda_handler:: Downloading account files {}:{}/{}.'.format(
            param_region,
            param_bucket,
            param_key))
        
        s3 = boto3.client('s3', region_name=param_region)
        response = s3.get_object(Bucket=param_bucket, Key=param_key)
        parameters_files = json.loads(response['Body'].read().decode('utf-8'))
    
    except Exception as e:
        print('ERROR! process_lambda_event:: Failed to download the Parameters Files.')
        print(e)
        raise(e)
    
    
    # For each account in the parameter files
    lambda_client = boto3.client('lambda')
    for account in parameters_files.keys():
        
        try:
            param = parameters_files[account]
            event = json.dumps({
                "IAMRole": param['IAMRole'],
                "Region": param['Region'],
                "Bucket": param['Bucket'],
                "Key": param['Key']
            })
            
            lambda_client.invoke(
                FunctionName=os.environ['LambdaFunctionName'],
                InvocationType='Event',
                Payload=event.encode()
            )
            
            print('INFO! lambda_event:: Invoked function with event: {}'.format(event))
            
        except Exception as e:
            print('INFO! lambda_event:: Error invoking function with account: {}.'.format(account))
            print(e)