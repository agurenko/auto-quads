#!/usr/bin/env python3
#
# Tool to obtain a self-scheduled quads environment
#
#  Copyright 2025 Red Hat
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.


import argparse
import base64
from datetime import datetime, timezone
import json
import os
import urllib3
import requests
import sys
import time


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")
MAX_EVENTS = 50


def utc_now():
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config():
  if not os.path.exists(CONFIG_PATH):
    return {
      "QUADS_SERVER": "",
      "QUADS_USERNAME": "",
      "QUADS_PASSWORD": "",
      "QUADS_OWNER": "",
      "QUADS_CLOUD": "",
      "QUADS_ASSIGNMENT_ID": "",
      "QUADS_DESCRIPTION": "",
      "QUADS_QINQ": 0,
      "QUADS_WIPE": True,
      "hosts": [],
      "events": [],
    }
  with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
    config = json.load(handle)
  config.setdefault("hosts", [])
  config.setdefault("events", [])
  return config


def save_config(config):
  with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
    json.dump(config, handle, indent=2)
    handle.write("\n")


def append_event(config, command, status, message):
  events = config.setdefault("events", [])
  events.append({
    "timestamp": utc_now(),
    "command": command,
    "status": status,
    "message": message,
  })
  config["events"] = events[-MAX_EVENTS:]
  save_config(config)


def record_host(config, hostname):
  hosts = config.setdefault("hosts", [])
  hosts.append({
    "hostname": hostname,
    "added_at": utc_now(),
  })
  config["hosts"] = hosts
  save_config(config)


def resolve_value(cli_value, env_key, config, cast=None, default=None):
  if cli_value is not None:
    return cli_value
  if env_key in os.environ:
    value = os.environ[env_key]
    if cast is not None:
      return cast(value)
    return value
  if env_key in config and config[env_key] not in (None, ""):
    value = config[env_key]
    if cast is not None:
      return cast(value)
    return value
  return default


def parse_bool(value):
  if isinstance(value, bool):
    return value
  if value is None:
    return False
  return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_int(value):
  if value is None or value == "":
    return None
  return int(value)


def fail(config, command, message, exit_code=1):
  print(message)
  append_event(config, command, "error", message)
  sys.exit(exit_code)


def add_host_to_cloud(cliargs, token, hostname, config):
  print("Adding host: {} to cloud: {}".format(hostname, cliargs.cloud))
  endpoint = "https://{}/api/v3/schedules".format(cliargs.quads_server)
  headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer {}".format(token)
  }
  payload = {
    "cloud": cliargs.cloud,
    "hostname": hostname
  }
  response = requests.post(endpoint, verify=False, headers=headers, json=payload)
  if response.status_code != 200 and response.status_code != 201:
    fail(
      config,
      "add-hosts",
      "Failed to add host {}: {} {}".format(hostname, response.status_code, response.text),
    )
  print("Host added to cloud successfully")
  record_host(config, hostname)
  append_event(config, "add-hosts", "success", "Added host {}".format(hostname))


def available_hosts(cliargs, config):
  total_available = 0
  print("Getting available hosts: \n")
  endpoint = "https://{}/api/v3/available?can_self_schedule=true".format(cliargs.quads_server)
  response = requests.get(endpoint, verify=False)
  if response.status_code != 200:
    fail(
      config,
      "list-available-hosts",
      "Failed to list available hosts: {} {}".format(response.status_code, response.text),
    )
  for host in response.json():
    print(host)
    total_available += 1
  print("\nTotal available hosts: {}".format(total_available))
  append_event(
    config,
    "list-available-hosts",
    "success",
    "Listed {} available hosts".format(total_available),
  )


def create_cloud(cliargs, token, config):
  print("Creating a new self-scheduled cloud\n")
  print("Description: {}".format(cliargs.description))
  print("Owner: {}".format(cliargs.owner))
  print("QinQ VLAN ID: {}".format(cliargs.qinq))
  print("Wipe the cloud: {}\n".format(cliargs.wipe))

  endpoint = "https://{}/api/v3/assignments/self".format(cliargs.quads_server)
  headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer {}".format(token)
  }
  payload = {
    "description": cliargs.description,
    "owner": cliargs.owner,
    "qinq": cliargs.qinq,
    "wipe": cliargs.wipe
  }
  response = requests.post(endpoint, verify=False, headers=headers, json=payload)
  if response.status_code != 201:
    fail(config, "create-cloud", "Failed to create cloud: {}".format(response.text))

  body = response.json()
  cloud_name = body["cloud"]["name"]
  assignment_id = body["notification"]["assignment_id"]
  print("Cloud created successfully\n")
  print("Cloud name: {}".format(cloud_name))
  print("Cloud Assignment ID: {}".format(assignment_id))

  config["QUADS_CLOUD"] = cloud_name
  config["QUADS_ASSIGNMENT_ID"] = str(assignment_id)
  save_config(config)
  append_event(
    config,
    "create-cloud",
    "success",
    "Created cloud {} assignment {}".format(cloud_name, assignment_id),
  )


def login(cliargs, config, command):
  endpoint = "https://{}/api/v3/login".format(cliargs.quads_server)
  headers = {
    "Content-Type": "application/json",
    "Authorization": "Basic {}".format(base64.b64encode(f"{cliargs.username}:{cliargs.password}".encode()).decode())
  }
  response = requests.post(endpoint, verify=False, headers=headers)
  if response.status_code != 200 and response.status_code != 201:
    fail(
      config,
      command,
      "Failed to login: {} {}".format(response.status_code, response.text),
    )
  token = response.json()["auth_token"]
  print("Logged in successfully")
  return token


def register_account(cliargs, config):
  print("Registering a new account")

  endpoint = "https://{}/api/v3/register".format(cliargs.quads_server)
  headers = {
    "Content-Type": "application/json"
  }
  payload = {
    "email": cliargs.username,
    "password": cliargs.password
  }
  response = requests.post(endpoint, verify=False, headers=headers, json=payload)
  if response.status_code != 200:
    fail(config, "register-account", "Failed to register: {}".format(response.text))
  print("Registered successfully")
  append_event(config, "register-account", "success", "Registered account {}".format(cliargs.username))


def terminate_cloud(cliargs, token, config):
  print("Terminating a cloud")
  print("Cloud name: {}".format(cliargs.cloud))
  endpoint = "https://{}/api/v3/assignments/terminate/{}".format(cliargs.quads_server, cliargs.assignment_id)
  headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer {}".format(token)
  }
  response = requests.post(endpoint, verify=False, headers=headers)
  print("Status code: {}".format(response.status_code))
  if response.status_code != 200:
    fail(config, "terminate-cloud", "Failed to terminate cloud: {}".format(response.text))
  print("Cloud terminated successfully")
  cloud_name = cliargs.cloud or config.get("QUADS_CLOUD", "")
  assignment_id = cliargs.assignment_id or config.get("QUADS_ASSIGNMENT_ID", "")
  config["QUADS_CLOUD"] = ""
  config["QUADS_ASSIGNMENT_ID"] = ""
  save_config(config)
  append_event(
    config,
    "terminate-cloud",
    "success",
    "Terminated cloud {} assignment {}".format(cloud_name, assignment_id),
  )


def wait_for_cloud(cliargs, config):
  print(f"Waiting for a cloud {cliargs.cloud} to complete validating")
  end_time = time.time() + cliargs.timeout
  endpoint = "https://{}/api/v3/assignments/{}".format(cliargs.quads_server, cliargs.assignment_id)
  headers = {
    "Content-Type": "application/json"
  }
  while time.time() < end_time:
    response = requests.get(endpoint, verify=False, headers=headers)
    if response.status_code != 200:
      fail(
        config,
        "wait-for-cloud",
        "Failed to wait for cloud: {} {}".format(response.status_code, response.text),
      )
    if str(response.json()["validated"]).lower() == "false":
      print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}: {cliargs.cloud} is not validated yet")
    else:
      print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}: {cliargs.cloud} is validated")
      append_event(
        config,
        "wait-for-cloud",
        "success",
        "Cloud {} validated".format(cliargs.cloud),
      )
      return
    time.sleep(cliargs.poll_interval)
  fail(
    config,
    "wait-for-cloud",
    "Timed out waiting for cloud {} to validate".format(cliargs.cloud),
  )


def resolve_settings(cliargs, config):
  cliargs.quads_server = resolve_value(cliargs.quads_server, "QUADS_SERVER", config)
  cliargs.username = resolve_value(cliargs.username, "QUADS_USERNAME", config)
  cliargs.password = resolve_value(cliargs.password, "QUADS_PASSWORD", config)
  cliargs.cloud = resolve_value(cliargs.cloud, "QUADS_CLOUD", config)
  cliargs.owner = resolve_value(cliargs.owner, "QUADS_OWNER", config)

  if hasattr(cliargs, "description"):
    cliargs.description = resolve_value(cliargs.description, "QUADS_DESCRIPTION", config)
  if hasattr(cliargs, "qinq"):
    cliargs.qinq = resolve_value(cliargs.qinq, "QUADS_QINQ", config, cast=parse_int, default=0)
  if hasattr(cliargs, "wipe"):
    cliargs.wipe = resolve_value(cliargs.wipe, "QUADS_WIPE", config, cast=parse_bool, default=False)
  if hasattr(cliargs, "assignment_id"):
    cliargs.assignment_id = resolve_value(cliargs.assignment_id, "QUADS_ASSIGNMENT_ID", config)
  if hasattr(cliargs, "timeout"):
    cliargs.timeout = resolve_value(cliargs.timeout, "QUADS_TIMEOUT", config, cast=parse_int, default=3600)
  if hasattr(cliargs, "poll_interval"):
    cliargs.poll_interval = resolve_value(cliargs.poll_interval, "QUADS_POLL_INTERVAL", config, cast=parse_int, default=10)


def main():
  start_time = time.time()
  config = load_config()

  parser = argparse.ArgumentParser(
      description="Tool to obtain a self-scheduled quads environment",
      prog="auto-quads.py", formatter_class=argparse.ArgumentDefaultsHelpFormatter)

  parser.add_argument("-s", "--quads-server", default=None, help="QUADS server hostname (env: QUADS_SERVER)")
  parser.add_argument("-u", "--username", default=None, help="Username for QUADS API (env: QUADS_USERNAME)")
  parser.add_argument("-p", "--password", default=None, help="Password for QUADS API (env: QUADS_PASSWORD)")
  parser.add_argument("-c", "--cloud", default=None, help="Cloud name (env: QUADS_CLOUD)")
  parser.add_argument("-o", "--owner", default=None, help="Cloud owner (env: QUADS_OWNER)")

  subparsers = parser.add_subparsers(dest="command")

  subparsers.add_parser("register-account",
      help="Register a new account", formatter_class=argparse.ArgumentDefaultsHelpFormatter)

  parser_create = subparsers.add_parser("create-cloud",
      help="Create a new self-scheduled cloud", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser_create.add_argument("-d", "--description", default=None, help="Cloud description (env: QUADS_DESCRIPTION)")
  parser_create.add_argument("-q", "--qinq", type=int, choices=[0, 1], default=None, help="QinQ VLAN ID (env: QUADS_QINQ)")
  parser_create.add_argument("-w", "--wipe", action=argparse.BooleanOptionalAction, default=None, help="Wipe the cloud (env: QUADS_WIPE)")

  subparsers.add_parser("list-available-hosts",
      help="List available hosts", formatter_class=argparse.ArgumentDefaultsHelpFormatter)

  parser_add = subparsers.add_parser("add-hosts",
      help="Add one or more hosts to a cloud", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser_add.add_argument("hostname", nargs='+', help="Hostname(s) to add to the cloud")

  parser_wait = subparsers.add_parser("wait-for-cloud",
      help="Wait for a cloud to complete validating", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser_wait.add_argument("-i", "--assignment-id", default=None, help="Assignment ID (env: QUADS_ASSIGNMENT_ID)")
  parser_wait.add_argument("-t", "--timeout", type=int, default=None, help="Timeout in seconds (env: QUADS_TIMEOUT)")
  parser_wait.add_argument("-p", "--poll-interval", type=int, default=None, help="Poll interval in seconds (env: QUADS_POLL_INTERVAL)")

  parser_terminate = subparsers.add_parser("terminate-cloud",
      help="Terminate a cloud", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser_terminate.add_argument("-i", "--assignment-id", default=None, help="Assignment ID (env: QUADS_ASSIGNMENT_ID)")
  cliargs = parser.parse_args()

  resolve_settings(cliargs, config)

  # Validate required arguments
  if not cliargs.quads_server:
    parser.error("--quads-server is required (set in env.json, QUADS_SERVER env var, or --quads-server)")

  # Auto determine owner value if not provided
  if cliargs.username and not cliargs.owner:
    cliargs.owner = cliargs.username.split("@")[0]

  if cliargs.command == "register-account":
    register_account(cliargs, config)
  elif cliargs.command == "create-cloud":
    token = login(cliargs, config, "create-cloud")
    create_cloud(cliargs, token, config)
  elif cliargs.command == "list-available-hosts":
    available_hosts(cliargs, config)
  elif cliargs.command == "add-hosts":
    token = login(cliargs, config, "add-hosts")
    for hostname in cliargs.hostname:
      add_host_to_cloud(cliargs, token, hostname, config)
  elif cliargs.command == "wait-for-cloud":
    wait_for_cloud(cliargs, config)
  elif cliargs.command == "terminate-cloud":
    token = login(cliargs, config, "terminate-cloud")
    terminate_cloud(cliargs, token, config)
  else:
    parser.print_help()
    sys.exit(1)

  end_time = time.time()
  total_time = round(end_time - start_time)
  print("\nTotal time: {} seconds".format(total_time))

if __name__ == "__main__":
  sys.exit(main())
