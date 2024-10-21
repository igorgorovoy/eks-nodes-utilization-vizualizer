import boto3
from kubernetes import client, config
from tqdm import tqdm
from colorama import Fore, Style


ec2_client = boto3.client('ec2', region_name='your-region')

config.load_kube_config()
v1 = client.CoreV1Api()

nodes = v1.list_node().items


def get_instance_type(node):
    for addr in node.status.addresses:
        if addr.type == "InternalIP":
            instance_id = addr.address
            return instance_id


def get_instance_details(instance_id):
    instance_details = ec2_client.describe_instances(InstanceIds=[instance_id])
    instance_type = instance_details['Reservations'][0]['Instances'][0]['InstanceType']
    pricing = get_instance_price(instance_type)
    return instance_type, pricing


def get_instance_price(instance_type):
    pricing_client = boto3.client('pricing', region_name='us-east-1')  # або інший регіон
    response = pricing_client.get_products(
        ServiceCode='AmazonEC2',
        Filters=[{'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type}]
    )
    # Парсинг отриманої ціни
    price = response['PriceList'][0]['terms']['OnDemand']
    return price


def get_node_utilization(node):
    cpu_allocatable = int(node.status.allocatable['cpu'].replace('n', '')) / 1000000  # Convert to cores
    memory_allocatable = int(node.status.allocatable['memory'].replace('Ki', '')) / (1024 * 1024)  # Convert to GiB
    cpu_capacity = int(node.status.capacity['cpu'].replace('n', '')) / 1000000
    memory_capacity = int(node.status.capacity['memory'].replace('Ki', '')) / (1024 * 1024)

    cpu_utilization = (cpu_allocatable / cpu_capacity) * 100
    memory_utilization = (memory_allocatable / memory_capacity) * 100
    return cpu_utilization, memory_utilization


def display_progress_bar(utilization, label):
    color = Fore.GREEN if utilization >= 90 else Fore.YELLOW if 60 <= utilization < 90 else Fore.RED
    print(f"{label}: {color}{utilization:.2f}%{Style.RESET_ALL}")

    tqdm_bar = tqdm(total=100, colour=color.replace(Fore, ""), bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}")
    tqdm_bar.n = utilization
    tqdm_bar.last_print_n = utilization
    tqdm_bar.refresh()
    tqdm_bar.close()

def analyze_nodes():
    for node in nodes:
        instance_id = get_instance_type(node)
        instance_type, price = get_instance_details(instance_id)
        cpu_utilization, memory_utilization = get_node_utilization(node)

        print(f"\nNode Address: {instance_id}")
        print(f"Instance Type: {instance_type}")
        print(f"Node Pricing: {price}")

        # Відображаємо прогрес-бари для утилізації CPU та пам'яті
        display_progress_bar(cpu_utilization, "CPU Utilization")
        display_progress_bar(memory_utilization, "Memory Utilization")



analyze_nodes()
