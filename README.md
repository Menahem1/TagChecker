# Tag Checker

<p align="center">
  <img src="images/Tag Checker-logo.png"/>
</p>

Tag Checker is a tool developed by the CCoE to check resources tags in AWS accounts and to notify you when a resource is not properly tagged. Tag Checker is designed to delegate to each account owner the ability to define their own tagging convention.


## Table of contents

 * [I. Architecture and principles](#i-architecture-and-principles)
 * [II. Configuration](#ii-configuration)
    * [1. For BU that want to use Tag Checker](#1-for-bu-that-want-to-use-tag-checker)
    * [2. To add new account in Tag Checker](#2-to-add-new-account-in-tag-checker)


## I. Architecture and principles
<p align="center">
  <img src="images/Tag Checker-Schema.png"/>
</p>

Two lambdas are executed for Tag Checker (Tag Checker & Tag Checker Child).

### Lambda IAM Role (`Lambda-TagChecker`)

Inline policy:

```
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject"
            ],
            "Resource": [
                "arn:aws:s3:::engie-infra-tagchecker/*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": [
                "sts:AssumeRole"
            ],
            "Resource": "arn:aws:iam::*:role/InfraAccount-DO_NOT_DELETE"
        },
        {
            "Effect": "Allow",
            "Action": [
                "lambda:InvokeFunction"
            ],
            "Resource": [
                "arn:aws:lambda:eu-west-1:782936146634:function:TagChecker-Child"
            ]
        }
    ]
}
```

### Lambda function

#### Parent
* Code: see parent.py
* Description: Fetch account to check and launch dedicated child lambda
* IAM role: Lambda-TagChecker
* Memory: 128 MB
* Timeout: 20 Secondes
* Environment variables
  * Bucket (value : engie-infra-tagchecker)
  * RegionName (value : eu-west-1) 
  * LambdaFunctionName (value : TagChecker-Child)
  * Key (value : accounts/account.json)
* Triggers:
  * CloudWatch Events - Schedule
  * Schedule expression : every 5mn


#### Child

* Code: see child.py
* Description: Check compliance of tag, then at the end send notifications
* IAM role: Lambda-TagChecker
* Memory: 128 MB
* Timeout: 5 minutes
* Environment variables
  * bucket_region (value : eu-west-1)
  * bucket_name (value : engie-infra-tagchecker)
* Triggers:
  * CloudWatch Events - Schedule
  * Schedule expression : every 5mn


#### How it works

There are different steps in the execution of the process

**1/** TagChecker retrieves the list of accounts to analyze from a JSON document in bucket `engie-infra-tagchecker`. Syntax of the JSON file (`accounts/accounts.json`):

```
[
  "Name Of Account": {
    "IAMRole":"arn:aws:iam::Account Number:role/InfraAccount-DO_NOT_DELETE",
    "Bucket": "Name of the bucket",
    "Region": "Which region the bucket is located",
    "Key": "key to the JSON of resource to check"
  },
  ...
]
```

With:
* `IAMRole`: the ARN of the IAM role that Tag Checker assumes to have the required permissions to query ResourceGroupsTagging, S3 and SNS APIs.
* `Bucket`: the name of the S3 bucket in the BU/BE account that contains the JSON document with the tagging convention
* `Region`: the region where this bucket resides
* `Key`: the key of the JSON object

For each entry in the JSON document, the parent Lambda function invokes the child Lambda function and passes the IAMRole, Bucket, Region and Key values to the child function.

**2/** Tag Checker - Child assumes the IAM role passed in the input event (IAMRole) to get temporary credentials and to be able to query the ResourceGroupsTagging API, get a JSON document in the S3 bucket that contains the tagging convention, and to send notifications to a SNS topic.

**3/** The child function retrieves and parses the JSON document with the tagging convention. Then, it queries the ResourceGroupsTagging API to list all supported resources in the account and their tags. Then it compares the actual tags with the desired tagging convention.

The JSON document that describes the expected should have the following format:

```
{
  "Checks": [
    {
      "TagKey": string,
      "TagValue": {
        "CheckValue": "True|False",
        "Type": "Regex|Simple",
        "PossibleValues": [string]
      },
      "Resources": [string],
      "Timeout": string
    },
    ...
  ],
  "SNSTopic": {
    "TopicARN": string,
    "Notif": "allinone|unique"
  }
}
```

With:
* `TagKey`: the key of the tag of the tag to check. Exemple: `BU`, `BE`, etc.
* `TagValue`:
  * `CheckValue`: set to `False` to check whether the resource has a tag whose key is `TagKey` whatever its value. Set `True` if you need to check the value.
  * If `CheckValue` = `True`:
    * Set `Type` to `Simple` if you want to check if the tag value is one of the values defined in a list, or to `Regex` if you would like to check if the tag value matches one of regex patterns.
    * `PossibleValues` is the:
      * list of possible values if `Type` = `Simple` (examples: `["True", "False"]`)
      * list of possible regex patterns if `Type` = `Regex` (examples: `["^BU[0-9]{2}$"]`). You can use a online regex tool to test your regex patterns like http://regexr.com/
* `Resources`: list of types of resources to check for that tag key and tag value (see `ResourceTypeFilters` in http://boto3.readthedocs.io/en/latest/reference/services/resourcegroupstaggingapi.html#ResourceGroupsTaggingAPI.Client.get_resources). You can enter `*` for all resources supported by ResourceGroupsTagging API, `service:*` for all resources of a service, `service:resource` for a specific type of resource.
* `Timeout`: Timeout parameter is used to indicate (in minutes) how many time before send a new notification if a notification has already been sent.
* `TopicARN`: ARN of the SNS topic where notifications for improperly tagged resources should be sent.
* `Notif`: You can define 2 types of notif:
  * `allinone` : will send you one notification with all items that are not correctly tagged
  * `unique` : will send you a notification for only one item that are not matching (so you can receive multiple notifications)

Example of JSON parameter:

```
{
  "Checks": [
    {
      "Resources": [
        "*"
      ],
      "TagKey": "BU",
      "TagValue": {
        "CheckValue": "True",
        "Type": "Simple",
        "PossibleValues": [
          "BU03"
        ]
      },
      "Timeout": "1440"
    },
    {
      "Resources": [
        "ec2:instance"
      ],
      "TagKey": "BackupPolicy",
      "TagValue": {
        "CheckValue": "False"
      },
      "Timeout": "14400"
    }
  ],
  "SNSTopic": {
    "TopicARN": "arn:aws:sns:eu-west-1:483090962619:NotifTagCheck",
    "Notif": "unique"
  }
}
```

**4/** The child function stores the history of notifications sent in a JSON document that resides in the bucket `engie-infra-tagchecker` with a object key `ACCOUNT-ID.json`. This history is needed to be able to wait `Timeout` minutes before re-sending a notification.

##  II. Configuration
### 1. For BU that want to use Tag Checker

**1/Create an IAM role:** In IAM you need to create a new role for cross account access (Provide access between AWS accounts you own) and provide the Account ID of AWS Infra Account (782936146634), then attach the managed policy `ResourceGroupsandTagEditorReadOnlyAccess` to role name `InfraAccount-DO_NOT_DELETE`.

In the IAM Role add a inline policy to enable the use of S3 and SNS for the lambda

```

{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject"
            ],
            "Resource": [
                "arn:aws:s3:::bucket-name/*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": [
                "SNS:Publish"
            ],
            "Resource": [
                "arn to sns topic"
            ]
        }
    ]
}
```

**2/Create a SNS topic:** Then create a SNS Topic with all the contacts that you want to notify

**3/Define the tagging convention:** If you don't have a bucket S3 for this type of use, create one with that recommanded syntax `name-of-entity-bucket`

Add in the bucket the JSON configuration file that will contain all the parameters that Tag Checker will use for find tag that are not matching.

Communicate to the CCoE the name of Bucket, Region, ARN of IAM Role, and key/path to file.json

### 2. To add new account in Tag Checker

Add a new entry in the file `accounts/accounts.json` with the information provided by the BU/BE.
