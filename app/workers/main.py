import time


def main() -> None:
    while True:
        print("support-agent worker heartbeat", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
