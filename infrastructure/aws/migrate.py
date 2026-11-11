"""Prepare or execute a single ECS migration from the next release image."""
import argparse
import re
import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stack', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'.+@sha256:[a-f0-9]{64}', args.image):
        parser.error('use an immutable image digest')
    if not args.execute:
        print('Plan: read stack outputs, register migration task with supplied image, run once in private subnets, wait and verify exit code. No AWS requests sent.')
        return
    cf, ecs = boto3.client('cloudformation'), boto3.client('ecs')
    stack = cf.describe_stacks(StackName=args.stack)['Stacks'][0]
    outputs = {x['OutputKey']:x['OutputValue'] for x in stack['Outputs']}
    definition = ecs.describe_task_definition(taskDefinition=outputs['MigrationTask'])['taskDefinition']
    allowed = ecs.meta.service_model.operation_model('RegisterTaskDefinition').input_shape.members
    registration = {k:v for k,v in definition.items() if k in allowed}
    for container in registration['containerDefinitions']:
        container['image'] = args.image
    task_definition = ecs.register_task_definition(**registration)['taskDefinition']['taskDefinitionArn']
    response = ecs.run_task(cluster=outputs['Cluster'], taskDefinition=task_definition, launchType='FARGATE',
        platformVersion='1.4.0', count=1, networkConfiguration={'awsvpcConfiguration':{
            'subnets':outputs['PrivateSubnets'].split(','), 'securityGroups':[outputs['TaskSecurityGroup']],
            'assignPublicIp':'DISABLED'}})
    if response.get('failures') or len(response.get('tasks',[])) != 1:
        raise RuntimeError('migration task did not start')
    arn = response['tasks'][0]['taskArn']
    ecs.get_waiter('tasks_stopped').wait(cluster=outputs['Cluster'], tasks=[arn],
        WaiterConfig={'Delay':10,'MaxAttempts':180})
    task = ecs.describe_tasks(cluster=outputs['Cluster'], tasks=[arn])['tasks'][0]
    if not task.get('containers') or any(c.get('exitCode') != 0 for c in task['containers']):
        raise RuntimeError('migration failed; inspect migration CloudWatch logs before deploying services')
    print('Migration succeeded:', arn)


if __name__ == '__main__':
    main()
