# Copyright 2013 (c) Noa Resare
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import os
import sys
import time


def double_fork(function, pid_file_path=None):
    """
    Does the double fork magic needed to fully decouple a process from it's
    calling environment.

    function - the function to call after detaching from the environment
    pid_file_path - the filename that the pid of the daemon will be written to
    """
    # open the pid_file in the parent, to get any exceptions propagated
    pid_file = None
    if pid_file_path:
        pid_file = open(pid_file_path, "w")

    pid = os.fork()
    if pid:
        if pid_file:
            pid_file.close()
        # wait for the intermediate child, so that we can be sure that the
        # pid file is written when the first child returns
        _, status = os.waitpid(pid, 0)
        assert status == 0
        return

    # Avoid umount problems by changing cwd to "/"
    os.chdir("/")

    # Make the intermediate process session leader.
    os.setsid()

    # Fork a second time, so that the parent is dead and the child gets
    # reaped by init. It also prevents the child from ever becoming a session
    # leader which would make it the receiver of some signals if/when
    # it opens an unattached terminal
    pid = os.fork()
    if pid:
        if pid_file:
            try:
                pid_file.write("%d\n" % pid)
            finally:
                pid_file.close()
        os._exit(0)

    if pid_file:
        pid_file.close()

    # replace stdio file fds with references to /dev/null
    devnull = open(os.devnull, "r+b")
    sys.stdin = devnull
    sys.stdout = devnull
    sys.stderr = devnull

    # call the provided function
    function()
    sys.exit(0)


if __name__ == '__main__':
    os.unlink("/tmp/pidfile")
    double_fork(lambda: time.sleep(100), "/tmp/pidfile")
    with open("/tmp/pidfile") as f:
        to_kill = int(f.read())
    print "read %d from pidfile" % to_kill
