#!/usr/bin/env python3

import argparse
import os
import re
import shutil
import subprocess
from multiprocessing import Pipe, Process
import time


def deal_with_args():
    parser = argparse.ArgumentParser(
        description="An eazy script for building mrs docker images"
    )
    parser.add_argument(
        "front_path",
        nargs="?",
        help="frontend project location",
        default=os.path.join(cwd, "material-reporting-front"),
    )
    parser.add_argument(
        "back_path",
        nargs="?",
        help="backend project location",
        default=os.path.join(cwd, "material-reporting-system"),
    )
    parser.add_argument(
        "--ver",
        type=str,
        required=True,
        help="image version",
    )
    parser.add_argument(
        "--force",
        type=bool,
        help="force to build ignoring image version",
        default=False,
    )
    parser.add_argument(
        "--url",
        type=str,
        help="image repository url",
        default="",
    )
    parser.add_argument(
        "--user",
        type=str,
        required=True,
        help="image repository username",
    )
    parser.add_argument(
        "--password",
        type=str,
        required=True,
        help="image repository password",
    )
    return parser.parse_args()


def build_front(pwd, college):
    print(f"building frontend project for {college}yuan...")
    os.chdir(pwd)

    # step1: copy router file
    print("step1: copy router file...")
    router_file = os.path.join(pwd, f"src/router/index{college}.js")
    dst_file = os.path.join(pwd, "src/router/index.js")
    shutil.copyfile(router_file, dst_file)

    # step2: replace api prefix
    print("step2: replace api prefix...")
    api_file = os.path.join(pwd, "src/api/global_variable.js")
    with open(api_file, "r+") as f:
        content = re.sub(
            r'const contextRoot =.*;',
            f'const contextRoot = "/dept{college}";',
            f.read(),
        )
        content = re.sub(
            r'const protocol =.*;',
            f'const protocol = "https://";',
            content,
        )
        f.seek(0)
        f.write(content)
        f.truncate()

    # step3: npm run build
    print("step3: npm run build...")
    subprocess.run(["npm", "run", "build"])
    src = os.path.join(pwd, "dist")
    dst = os.path.join(pwd, f"dist{college}")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.move(src, dst)
    return dst


def build_back(pwd, college, dist_path):
    print(f"building backend project for {college}yuan...")
    os.chdir(pwd)

    # step1: copy dist files
    print("step1: copy dist files...")
    static_path = os.path.join("src/main/resources/static")
    shutil.copytree(dist_path, static_path, dirs_exist_ok=True)

    # step2: switch application.properties
    print("step2: switch application.properties...")
    config_file_bk = os.path.join(pwd, "src/main/resources/application.properties.bk")
    config_file = os.path.join(pwd, "src/main/resources/application.properties")
    shutil.copyfile(config_file_bk, config_file)
    with open(config_file, "r+") as f:
        content = re.sub(
            rf"#spring.profiles.active={college}yuan",
            f"spring.profiles.active={college}yuan",
            f.read(),
        )
        f.seek(0)
        f.write(content)
        f.truncate()

    # step3: maven build jar
    print("step3: maven build jar...")
    subprocess.run(["mvn", "clean", "package"])

    # step4: copy jar file
    print("step4: copy jar file...")
    jar_src = os.path.join(pwd, "target/mrs-0.0.1-SNAPSHOT.jar")
    jar_dst = os.path.join(pwd, f"{college}yuan-mrs-0.0.1-SNAPSHOT.jar")
    shutil.copyfile(jar_src, jar_dst)
    return jar_dst

def push_docker_image(tag_name, retries=3, delay=5):
    for attempt in range(retries):
        try:
            print(f"Attempt {attempt + 1} to push {tag_name}...")
            result = subprocess.run(["docker", "push", tag_name], check=True)
            print("Push successful!")
            return result
        except subprocess.CalledProcessError as e:
            print(f"Error pushing image: {e}. Retrying in {delay} seconds...")
            time.sleep(delay)
    print("All attempts failed.")
    return None

def build_docker(pwd, college, jar_path, ver, url, user):
    print(f"building docker image for {college}yuan...")
    os.chdir(pwd)

    # step1: copy jar
    print("step1: copy jar...")
    jar_file = os.path.join(pwd, "mrs-0.0.1-SNAPSHOT.jar")
    shutil.copyfile(jar_path, jar_file)

    # step2: build image
    tag_name = f"{url+'/' if url else ''}{user}/guet-mrs:{college}.{ver}"
    print(f"step2: build image: {tag_name}...")
    subprocess.run(["docker", "build", "-t", tag_name, "."])

    # step3: push image
    print(f"step3: push image: {tag_name} ...")
    push_docker_image(tag_name)


def check_images(college, ver, url, user):
    print(f"checking image for {college}yuan...")
    tag_name = f"{url+'/' if url else ''}{user}/guet-mrs:{college}.{ver}"

    result = subprocess.run(
        ["docker", "images", "-q", tag_name], capture_output=True, text=True
    )
    result = bool(result.stdout.strip())
    if result:
        print(f"* image {tag_name} already exists.")
    return result


def front_work(pwd, tasks, distSnd):
    try:
        for college in tasks:
            dist = build_front(pwd, college)
            distSnd.send((college, dist))
    finally:
        distSnd.send(None)
        distSnd.close()
        print("* all frontend tasks finished.")
        print("* frontend process exit.")


def back_work(pwd, distRcv, jarSnd):
    while True:
        try:
            distTask = distRcv.recv()
            if distTask is None:
                raise EOFError
            jar = build_back(pwd, distTask[0], distTask[1])
            jarSnd.send((distTask[0], jar))
        except EOFError:
            jarSnd.send(None)
            jarSnd.close()
            print("* all backend tasks finished.")
            print("* backend process exit.")
            break


def image_work(pwd, ver, url, user, passwd, jarRcv):
    first = True
    while True:
        try:
            jarTask = jarRcv.recv()
            if jarTask is None:
                raise EOFError
            # first build image needed login.
            if first:
                print("* login docker...")
                subprocess.run(["docker", "login", url, "-u", user, "-p", passwd])
                first = False
            build_docker(pwd, jarTask[0], jarTask[1], ver, url, user)
        except EOFError:
            print("* all image tasks finished.")
            print("* image process exit.")
            break


if __name__ == "__main__":
    cwd = os.getcwd()
    args = deal_with_args()
    front_path = args.front_path
    back_path = args.back_path
    url = args.url
    force = args.force
    ver = args.ver
    user = args.user
    passwd = args.password

    print("* current in path:", cwd)
    print("* using frontend project path:", front_path)
    print("* using backend project path:", back_path)
    print("* using image repository url:", url)
    print("* using image repository user:", user)
    print("* using image version:", ver)
    print("* using force:", force)

    colleges = ["2", "3", "7", "10", "17"]
    # colleges = ["3"]
    tasks = []

    distSnd, distRcv = Pipe()
    jarSnd, jarRcv = Pipe()
    tasks.extend(
        college
        for college in colleges
        if force or not check_images(college, ver, url, user)
    )
    processes = [
        Process(target=front_work, args=(front_path, tasks, distSnd)),
        Process(target=back_work, args=(back_path, distRcv, jarSnd)),
        Process(target=image_work, args=(back_path, ver, url, user, passwd, jarRcv)),
    ]
    [p.start() for p in processes]
    [p.join() for p in processes]
