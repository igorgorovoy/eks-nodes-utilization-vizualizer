import time
import json
import boto3
from kubernetes import client, config
from colorama import Fore

aws_region = 'eu-west-1'
pricing_region = 'us-east-1'

ec2_client = boto3.client('ec2', region_name=aws_region)

# Завантаження конфігурації Kubernetes для конкретного контексту
config.load_kube_config(context="arn:aws:eks:eu-west-1:294949574448:cluster/dev-1-30")
v1 = client.CoreV1Api()

# Отримуємо всі ноди
nodes = v1.list_node().items


# Функція для отримання Instance ID з анотацій ноди
def get_instance_id(node):
    annotations = node.metadata.annotations
    if 'node.kubernetes.io/instance-id' in annotations:
        return annotations['node.kubernetes.io/instance-id']
    else:
        return get_instance_id_by_internal_ip(node)


# Функція для отримання Instance ID за InternalIP
def get_instance_id_by_internal_ip(node):
    for addr in node.status.addresses:
        if addr.type == "InternalIP":
            internal_ip = addr.address
            response = ec2_client.describe_instances(
                Filters=[{
                    'Name': 'private-ip-address',
                    'Values': [internal_ip]
                }]
            )
            if response['Reservations']:
                return response['Reservations'][0]['Instances'][0]['InstanceId']
    return None


# Функція для отримання деталей інстансу EC2
def get_instance_details(instance_id):
    instance_details = ec2_client.describe_instances(InstanceIds=[instance_id])
    instance_type = instance_details['Reservations'][0]['Instances'][0]['InstanceType']
    price = get_instance_price(instance_type)
    return instance_type, price


# Функція для отримання ціни інстансу
def get_instance_price(instance_type):
    # Виконуємо запит до Pricing API
    pricing_client = boto3.client('pricing', region_name=pricing_region)
    response = pricing_client.get_products(
        ServiceCode='AmazonEC2',
        Filters=[
            {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type},
            {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': 'EU (Ireland)'}
        ]
    )

    # Перевіряємо наявність результатів
    if 'PriceList' in response and response['PriceList']:
        for price_item in response['PriceList']:
            price_item_dict = json.loads(price_item)  # Перетворюємо рядок JSON в словник
            if 'terms' in price_item_dict:
                price_dimensions = price_item_dict['terms']['OnDemand']
                for key in price_dimensions:
                    price = price_dimensions[key]['priceDimensions']
                    for price_key in price:
                        return float(price[price_key]['pricePerUnit']['USD'])  # Повертаємо ціну як float
    return 0.0  # Повертаємо 0.0, якщо не вдалося отримати ціну


# Функція для отримання утилізації CPU та пам'яті
def get_node_utilization(node):
    cpu_allocatable_str = node.status.allocatable['cpu']
    cpu_allocatable = int(cpu_allocatable_str.replace('m', '')) / 1000 if 'm' in cpu_allocatable_str else int(
        cpu_allocatable_str)
    memory_allocatable = int(node.status.allocatable['memory'].replace('Ki', '')) / (1024 * 1024)

    cpu_capacity_str = node.status.capacity['cpu']
    cpu_capacity = int(cpu_capacity_str.replace('m', '')) / 1000 if 'm' in cpu_capacity_str else int(cpu_capacity_str)
    memory_capacity = int(node.status.capacity['memory'].replace('Ki', '')) / (1024 * 1024)

    cpu_utilization = (cpu_allocatable / cpu_capacity) * 100
    memory_utilization = (memory_allocatable / memory_capacity) * 100
    return cpu_utilization, memory_utilization, cpu_capacity, memory_capacity


# Функція для виведення прогрес-бару
def display_progress_bar(value):
    bar_length = 20  # Довжина прогрес-бару
    filled_length = int(bar_length * (value / 100))
    bar = '█' * filled_length + ' ' * (bar_length - filled_length)

    # Визначення кольору на основі утилізації
    color = Fore.RED if value < 30 else Fore.YELLOW if value < 90 else Fore.GREEN

    return f"{color}[{bar}] {value:.2f}%{Fore.RESET}"


# Основна функція для аналізу нод
def analyze_nodes():
    while True:
        total_cpu_utilization = 0
        total_memory_utilization = 0
        total_cpu_capacity = 0
        total_memory_capacity = 0
        total_cost = 0.0
        node_count = 0
        node_data = []  # Зберігаємо дані про ноди

        for node in nodes:
            instance_id = get_instance_id(node)
            if instance_id:
                instance_type, price = get_instance_details(instance_id)
                cpu_utilization, memory_utilization, cpu_capacity, memory_capacity = get_node_utilization(node)

                # Зберігаємо дані про ноду
                node_data.append({
                    "name": node.metadata.name,
                    "instance_type": instance_type,
                    "price": price,
                    "cpu_capacity": cpu_capacity,
                    "memory_capacity": memory_capacity,
                    "cpu_utilization": cpu_utilization,
                    "memory_utilization": memory_utilization
                })

                # Накопичуємо загальні значення
                total_cpu_utilization += cpu_utilization
                total_memory_utilization += memory_utilization
                total_cpu_capacity += cpu_capacity
                total_memory_capacity += memory_capacity
                total_cost += price  # Додаємо ціну до загальної вартості
                node_count += 1
            else:
                print(f"Error: Could not retrieve instance ID for node {node.metadata.name}")

        # Обчислюємо загальну утилізацію
        if node_count > 0:
            avg_cpu_utilization = total_cpu_utilization / node_count
            avg_memory_utilization = total_memory_utilization / node_count

            # Вивід транспонованих даних з форматуванням
            print("\n" + "-" * 130)
            print(
                f"{'Node Name':<30} | {'Instance Type':<20} | {'Node Pricing':<15} | {'CPU Capacity':<15} | {'Memory Capacity':<15} | {'CPU Utilization':<20} | {'Memory Utilization':<20}")
            print("-" * 130)
            for data in node_data:
                cpu_bar = display_progress_bar(data["cpu_utilization"])
                memory_bar = display_progress_bar(data["memory_utilization"])

                print(
                    f"{data['name']:<30} | {data['instance_type']:<20} | ${data['price']:.4f}/hour     | {data['cpu_capacity']:<15} | {data['memory_capacity']:<15} | {cpu_bar} | {memory_bar}")

            print("-" * 130)
            print(f"\nAverage CPU Utilization for all nodes: {avg_cpu_utilization:.2f}%")
            print(f"Average Memory Utilization for all nodes: {avg_memory_utilization:.2f}%")
            print(f"Total Nodes: {node_count}")
            print(f"Total CPU Capacity: {total_cpu_capacity:.2f} vCPUs")
            print(f"Total Memory Capacity: {total_memory_capacity:.2f} GiB")
            print(f"Total Cost for all nodes: ${total_cost:.4f}/hour")  # Виведення загальної вартості
        else:
            print("\nNo nodes found for utilization analysis.")

        print("\nPress Ctrl+C to quit...")
        time.sleep(5)  # Затримка перед повторним аналізом


# Викликаємо функцію аналізу нод
try:
    analyze_nodes()
except KeyboardInterrupt:
    print("\nExiting...")