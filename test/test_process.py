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
import time
import tempfile
import signal
import os
from runasdaemon import process


def test_double_fork():
    _, pid_file = tempfile.mkstemp()
    process.double_fork(lambda: time.sleep(10), pid_file)
    with open(pid_file) as f:
        to_kill = int(f.read())
    os.unlink(pid_file)

    os.kill(to_kill, signal.SIGTERM)
