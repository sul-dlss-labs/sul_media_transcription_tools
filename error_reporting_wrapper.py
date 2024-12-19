#!/usr/bin/env python3

import dotenv
import logging
import os
import sys

from honeybadger import honeybadger
from subprocess import run, CalledProcessError


# This must be invoked before the logger is invoked for the first time
def configure() -> None:
    dotenv.load_dotenv()

    # TODO: HB and logging config copied from speech_to_text.py, may eventually want
    # to centralize more and start organizing codebase as a package?
    honeybadger.configure(
        api_key=os.environ.get("HONEYBADGER_API_KEY", ""),
        environment=os.environ.get("HONEYBADGER_ENV", "stage"),
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s :: %(levelname)s :: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


# advantages of this vs using the `honeybadger exec` subcommand
# of the honeybadger gem/command (see https://docs.honeybadger.io/lib/ruby/gem-reference/cli/):
# 1. a little more control over what the error reporting sends along
# 2. more honest reporting of error code: in my testing, a script that exited with a
#    return code of '1' was reported as having a return code of '256' by honeybadger
#    exec (but was correctly reported by this, when compared to running the command by itself and doing `echo $?`)
# 3. this script will bubble up the return code of the wrapped script, whereas honeybadger exec always returns 0,
#    even when the wrapped script exits non-zero
# 4. one or two fewer dependencies to add to the docker image (maybe ruby, depending on the base image; definitely
#    the honeybadger gem regardless, whereas we already have the python package in our python deps)
#
# disadvantages of this approach: a little more code of our own to maintain
def run_with_error_reporting(cmd_with_args: list) -> int:
    returncode: int

    try:
        completed_process = run(cmd_with_args, check=True)
        returncode = completed_process.returncode

        logging.info(completed_process)
    except KeyboardInterrupt:
        logging.info(f"exiting {sys.argv[0]}")
        sys.exit()
    except CalledProcessError as e:
        returncode = e.returncode

        error_context = {
            "message": str(e),
            "cmd": e.cmd,
            "returncode": e.returncode,
        }
        logging.error(error_context)
        honeybadger.notify(e, context=error_context)

    return returncode


if __name__ == "__main__":
    configure()

    cmd_with_args = sys.argv[1:]  # argv[0] is this script's name
    logging.info(f"command and args: {cmd_with_args}")

    # bubble up the exit code from the wrapped call
    sys.exit(run_with_error_reporting(cmd_with_args))
