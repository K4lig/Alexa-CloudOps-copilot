"""Tienda de prueba: simula visitas y envía métricas reales a Amazon CloudWatch.

    python shop/simulate_traffic.py            # tráfico normal
    python shop/simulate_traffic.py --chaos    # checkout-api empieza a fallar

Cada 10 segundos, por cada servicio, envía tres métricas:
  Requests (visitas), Errors (visitas que fallaron) y LatencyMs (tiempo promedio).
"""

import argparse
import random
import time

import boto3

NAMESPACE = "CloudOpsCopilot/Demo"
SERVICES = ["checkout-api", "inventory-svc", "web-frontend"]
INTERVAL_SECONDS = 10


def one_batch(service: str, chaos: bool) -> dict:
    """Inventa 10 segundos de tráfico para un servicio."""
    requests = random.randint(40, 60)
    broken = chaos and service == "checkout-api"
    error_rate = random.uniform(0.10, 0.14) if broken else random.uniform(0.0, 0.006)
    latency = random.uniform(1800, 2600) if broken else random.uniform(90, 160)
    return {"requests": requests, "errors": round(requests * error_rate), "latency_ms": latency}


def send(cloudwatch, service: str, batch: dict) -> None:
    dims = [{"Name": "Service", "Value": service}]
    cloudwatch.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
            {"MetricName": "Requests", "Dimensions": dims, "Value": batch["requests"], "Unit": "Count"},
            {"MetricName": "Errors", "Dimensions": dims, "Value": batch["errors"], "Unit": "Count"},
            {"MetricName": "LatencyMs", "Dimensions": dims, "Value": batch["latency_ms"], "Unit": "Milliseconds"},
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chaos", action="store_true", help="checkout-api falla a propósito")
    args = parser.parse_args()

    cloudwatch = boto3.client("cloudwatch")
    print(f"Enviando métricas a CloudWatch ({NAMESPACE}) cada {INTERVAL_SECONDS}s. Chaos: {args.chaos}. Ctrl+C para parar.")
    while True:
        for service in SERVICES:
            batch = one_batch(service, args.chaos)
            send(cloudwatch, service, batch)
            print(f"  {service:14} requests={batch['requests']:3} errors={batch['errors']:2} latency={batch['latency_ms']:.0f}ms")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
