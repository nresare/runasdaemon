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
from __future__ import print_function

import os
import socket
import subprocess
import syslog
import fcntl
import select
import sys
import signal
import time
import errno
import StringIO


def syslog_log(msg):
    """
    Write msg to syslog on level LOG_NOTICE.
    """
    msg = msg.rstrip()
    syslog.syslog(syslog.LOG_NOTICE, msg)


class ChildRunner(object):
    """
    Instances of ChildRunner spawns a child as specified in cmdline and
    opens a unix domain socket located at socket_path.

    The sockets connects to stdin and stdout of the child and can be
    connected to and disconnected from without affecting the child process.
    stderr form the child gets sent to the socket intermingled with stdout.

    If exit_word is specified, it is sent to stdin of the child process when
    SIGTERM is sent to the current process. If exit_word is not specified,
    SIGTERM is propagated to the child.

    parent_fd, if set is, the write end of a pipe to the parent process that
    this child runner holds open for parent_fd_linger_secs. If the cmdline
    exits before that time, the exit status of the process, encoded in base 10
    ascii, terminated by newline is written to the socket. If there were any
    output to stderr of the exiting process, that information is sent after the
    exit status, terminated by newline. When parent_fd_linger_secs have passed
    since the constructor was called, the socket is closed.

    If the process doesn't die in 5 seconds SIGKILL is sent.

    """

    def __init__(self, cmdline, socket_path, exit_word=None, log=syslog_log,
                 parent_fd=None, parent_fd_linger_secs=1):
        self.read_list = []
        self.log = log
        self.parent_fd = parent_fd
        self.parent_fd_expiry = time.time() + parent_fd_linger_secs
        self.stderr_buffer = None
        if parent_fd:
            self.stderr_buffer = StringIO.StringIO()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.setblocking(True)
        self.log("binding ChildRunner unix socket to '%s'" % socket_path)
        try:
            s.bind(socket_path)
        except socket.error, e:
            raise RuntimeError("Failed to bind socket to path '%s': %s"
                               % (socket_path, str(e)))
        s.listen(0)

        self.server_socket = s
        self.read_list.append(self.server_socket)

        if isinstance(cmdline, basestring):
            cmdline = (cmdline,)
        self.cmdline = " ".join(cmdline)
        self.log("Starting %s" % " ".join(cmdline))
        try:
            p = subprocess.Popen(cmdline, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        except OSError, e:
            raise OSError("Failed to start '%s': %s"
                          % (" ".join(cmdline), str(e)))
        _set_nonblock(p.stdout)
        _set_nonblock(p.stderr)
        self.child_pipe = p
        self.read_list.append(p.stdout)
        self.read_list.append(p.stderr)

        self.comm_socket = None
        self.exit_word = exit_word
        signal.signal(signal.SIGTERM, self._term_handler)
        self.hard_exit = 0

    def select(self):

        while True:
            try:
                to_read, _, _ = select.select(self.read_list, [], [], 1.0)
            except select.error, e:
                if e[0] != errno.EINTR:
                    raise
            if self.parent_fd and self.parent_fd_expiry < time.time():
                os.close(self.parent_fd)
                self.stderr_buffer = None
                self.parent_fd = None
            if self.hard_exit and self.hard_exit < time.time():
                self.log("Graceful shutdown of child process failed. "
                         "Sending SIGKILL")
                self.child_pipe.kill()
                self.hard_exit = 0
            if self.server_socket in to_read:
                self.comm_socket, _ = self.server_socket.accept()
                self.read_list.append(self.comm_socket)
            if self.comm_socket in to_read:
                buf = self.comm_socket.recv(1024)
                if not len(buf):
                    self.read_list.remove(self.comm_socket)
                    self.comm_socket = None
                    continue
                self.child_pipe.stdin.write(buf)
            if not self.child_pipe:
                # switch cases below this line expects self.child_pipe, let's
                # skip them if child_pipe
                # is not set.
                continue
            if self.child_pipe.stdout in to_read:
                buf = self.child_pipe.stdout.read()
                if not len(buf):
                    code = self.child_pipe.wait()
                    self.child_pipe.wait()
                    s = ("Child process '%s' exited with status %s, exiting."
                         % (self.cmdline, code))
                    self.log(s)
                    if self.parent_fd:
                        s = self.stderr_buffer.getvalue()
                        s = "\n" + s if s else ""
                        os.write(self.parent_fd, "%d%s" % (code, s))
                        os.close(self.parent_fd)
                    return code
                self._handle_child_output(buf)
            if self.child_pipe.stderr in to_read:
                buf = None
                try:
                    buf = self.child_pipe.stderr.read()
                except IOError, e:
                    if e.errno != errno.EAGAIN:
                        raise
                if self.stderr_buffer:
                    self.stderr_buffer.write(buf)
                self._handle_child_output(buf)

    def run_forever(self):
        sys.exit(self.select())

    def _handle_child_output(self, buf):
        self.log(buf)
        if self.comm_socket:
            self.comm_socket.send(buf)

    def _term_handler(self, *_):
        self.log("Caught SIGTERM, attempting graceful shutdown.")
        if self.exit_word:
            self.log("Sending '%s'" % self.exit_word)
            self.child_pipe.stdin.write("%s\n" % self.exit_word)
        else:
            self.log("Propagating TERM signal to child")
            self.child_pipe.terminate()
        self.hard_exit = time.time() + 5.0


def _send_all(buf, sock):
    sent = 0
    while sent < len(buf):
        count = sock.send(buf[sent:])
        if sent == 0:
            raise Exception("failed to send data")
        sent += count


def _set_nonblock(f):
    # sets the O_NONBLOCK flag on the underlying fd of the given file
    fd = f.fileno()
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    if not fl & os.O_NONBLOCK:
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
