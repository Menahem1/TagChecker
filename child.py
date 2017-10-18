import boto3
import json
import re
import os
import datetime
import time


def lambda_handler(event, context):
    
    print('INFO! lambda_handler:: Event Received:')
    print(json.dumps(event))
    
    #STS connection for assume role operation
    sts_connection = boto3.client('sts')
    try:
        assumedRoleObject = sts_connection.assume_role(
            RoleArn=event['IAMRole'],
            RoleSessionName='TagChecker-Child')
            
        access_key = assumedRoleObject['Credentials']['AccessKeyId']
        secret_access_key = assumedRoleObject['Credentials']['SecretAccessKey']
        session_token = assumedRoleObject['Credentials']['SessionToken']
        
    except Exception as e:
        print(e)
        raise(e)
      
    invalid_resources, notif = process_account(access_key, secret_access_key, session_token, event['Region'], event['Bucket'], event['Key'])

    account_id = event['IAMRole'].split(':')[4]
    send_notifs(invalid_resources, notif, account_id, access_key, secret_access_key, session_token)
    

def process_account(access_key, secret_access_key, session_token, region, bucket, key):

    try:

        # Get on resourcegrouptaggingapi tag name of resources 
        resources_tags = []
        resourcegroup = boto3.client('resourcegroupstaggingapi', aws_access_key_id=access_key, aws_secret_access_key=secret_access_key, 
        aws_session_token=session_token)
    
        # Current botocore version used by Lambda does not paginate
        if resourcegroup.can_paginate('get_resources'):
            paginator = resourcegroup.get_paginator('get_resources')
            response_iterator = paginator.paginate(TagsPerPage=500)
            for t in response_iterator:
                if 'ResourceTagMappingList' in t:
                    resources_tags += t['ResourceTagMappingList']
    
        else:
            nextToken = ''
            while True:
                t = resourcegroup.get_resources(TagsPerPage=100, PaginationToken='')
                if 'ResourceTagMappingList' in t:
                    resources_tags += t['ResourceTagMappingList']
                nextToken = t['PaginationToken']
                if nextToken == '':
                    break
                
        print('INFO! process_account:: {} resources returned by ResourceGroupsTaggingAPI'.format(len(resources_tags)))

    except Exception as e:
        print('ERROR! process_account:: Failed to retrieve resources and tags')
        print(e)
        raise(e)
    
    # Get JSON parameter file  of account
    try:
        s3 = boto3.client('s3',
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_access_key, 
            aws_session_token=session_token)
            
        response = s3.get_object(Bucket=bucket, Key=key)
        client_param_json = json.loads(response['Body'].read().decode('utf-8'))
        print(json.dumps(client_param_json))
      
    except Exception as e:
        print('ERROR! process_account:: Failed to download and parse the JSON client file in S3')
        print(e)
        raise(e)

    # Put in list (invalid_resource) all resources that are not matching
    try:
        invalid_resources = []
        for check in client_param_json['Checks']:
            for check_resource in check['Resources'] :
                
                for resource in resources_tags:
                    # Check for resources like '*', 'ec2:*', 'ec2:instance'
                    if (
                    check_resource == "*"
                    or (resource['ResourceARN'].split(':')[2] == check_resource.split(':')[0] and check_resource.split(':')[1] == '*')
                    or (resource['ResourceARN'].split(':')[2] == check_resource.split(':')[0] and '/' not in resource['ResourceARN'])
                    or (resource['ResourceARN'].split(':')[2] == check_resource.split(':')[0] and resource['ResourceARN'].split(':')[5].split('/')[0] == check_resource.split(':')[1])
                    ):
                        invalid_resources += check_tag(resource, check)
        
    except Exception as e:
        print(e)
        raise(e)
      
    
    print('INFO! process_account:: List of invalid resources:')
    print(json.dumps(invalid_resources))
    return (invalid_resources, client_param_json['SNSTopic'])
    
      
def check_tag(resource, check):
    error = None
    
    # Check missing tag
    if (check['TagKey'] not in [i['Key'] for i in resource['Tags']]):
        error = {
            'ResourceARN': resource['ResourceARN'],
            'TagKey': check['TagKey'],
            'Reason': 'missing_tag',
            'Timeout': check['Timeout']
        }
    
    # Check incorrect tag value
    else:
        for tag in resource['Tags']:
            if tag['Key'] == check['TagKey'] and check['TagValue']['CheckValue'] == "True":

                if (check['TagValue']['Type'] == "Simple" and tag['Value'] not in check['TagValue']['PossibleValues']): 
                    error = {
                        'ResourceARN': resource['ResourceARN'],
                        'TagKey': check['TagKey'],
                        'TagValue': tag['Value'],
                        'Reason': 'invalid_value',
                        'Timeout': check['Timeout']
                    }
                    
                if check['TagValue']['Type'] == "Regex":
                    matched = False
                    for pattern in check['TagValue']['PossibleValues']:
                        if re.match(pattern, tag['Value']):
                            matched = True
                            break
                    if not matched:
                        error = {
                            'ResourceARN': resource['ResourceARN'],
                            'TagKey': check['TagKey'],
                            'TagValue': tag['Value'],
                            'Reason': 'invalid_value',
                            'Timeout': check['Timeout']
                        }
    
    if error == None:
        return []
    else:
        return [error]


def send_notifs(invalid_resources, topic, account_id, access_key, secret_access_key, session_token):
    
    # Get history of last notifications send, for check if timeout is < to now
    try:
        s3 = boto3.client('s3', region_name=os.environ['bucket_region'])
        s3.download_file(os.environ['bucket_name'], 'history/'+account_id+'.json', '/tmp/'+account_id+'.json')
        print('INFO! send_notifs:: History JSON downloaded from S3')
        
    except Exception as e:
        json_empty = {}
        with open('/tmp/'+account_id+'.json', 'w') as f:
            json.dump(json_empty, f, ensure_ascii=False)
        print('INFO! send_notifs:: New empty history JSON created')


    with open('/tmp/'+account_id+'.json') as data_file:    
        history = json.load(data_file)
        
    # notifs_to_send is a list that will contain all messages to be sent
    notifs_to_send = []
    # history_changed for know if necessary to update config file of clients in S3
    history_changed = False
    
    for resource in invalid_resources:
        history_key = '{}-{}'.format(resource['TagKey'], resource['ResourceARN'])
     
        if history_key in history.keys():
            last_date_str = history[history_key]
            last_date = datetime.datetime.strptime(last_date_str, "%Y-%m-%d %H:%M:%S.%f")
            delta_minutes = (datetime.datetime.utcnow() - last_date).days * 24 * 60
            if delta_minutes < int(resource['Timeout']):
                continue
        
        history[history_key] = str(datetime.datetime.utcnow() + datetime.timedelta(minutes = int(resource['Timeout'])))
        del resource['Timeout']
        notifs_to_send.append(resource)
        history_changed = True
            
            
    if topic['Notif'] == 'allinone' and len(notifs_to_send) > 0:
        message = json.dumps({'errors': notifs_to_send})
        message_txt = 'List of resources with missing or invalid tags:'
        for notif_to_send in notifs_to_send:
            message_txt += '\n- '
            for i in notif_to_send.keys():
                message_txt += '{}: {}, '.format(i, notif_to_send[i])
        send_sns(access_key, secret_access_key, session_token, topic['TopicARN'], message, message_txt)
        
    else:
        for notif_to_send in notifs_to_send:
            message = json.dumps(notif_to_send)
            message_txt = ''
            for i in notif_to_send.keys():
                message_txt += '{}: {}\n'.format(i, notif_to_send[i])
            send_sns(access_key, secret_access_key, session_token, topic['TopicARN'], message, message_txt)
    
    
    if history_changed:
        s3.put_object(
            Bucket=os.environ['bucket_name'],
            Key='history/'+account_id+'.json',
            Body=json.dumps(history)
        )
        print('INFO! send_notifs:: History JSON uploaded to {}'.format('history/'+account_id+'.json'))
        

def send_sns(access_key, secret_access_key, session_token, arntopic, message, message_txt):
    # Send in format readable for email and in JSON for other destination (like lambda)
    try:
        sns = boto3.client('sns', aws_access_key_id=access_key, aws_secret_access_key=secret_access_key, aws_session_token=session_token)
        response = sns.publish(
            TopicArn=arntopic,
            Message=json.dumps({
                "default":message,
                "email":message_txt
            }),
            Subject='TagChecker Notification',
            MessageStructure='json'
        )
        print('INFO! send_sns:: Message {}'.format(message))

    except Exception as e:
        print('ERROR! send_sns:: Failed to send message')
        print(e)
