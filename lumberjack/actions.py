# -*- coding: utf-8 -*-
#
# This file is part of Lumberjack.
# Copyright 2014 CERN.
#
# Lumberjack is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# Lumberjack is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Lumberjack.  If not, see <http://www.gnu.org/licenses/>.

"""Provide the ActionQueue class."""

from __future__ import absolute_import

from elasticsearch import ElasticsearchException, TransportError
from elasticsearch.helpers import bulk
from threading import Thread, Event, Lock
from json import dumps
import traceback
import logging
from copy import deepcopy


class ActionQueue(Thread):

    """Hold a queue of actions and a thread to bulk-perform them.

    This is instantiated automatically by the ``lumberjack.Lumberjack`` object.
    It will keep a queue of indexing actions to be performed in Elasticsearch,
    and perform them bulk ('flush') when one of three things happens:

    1. It has waited ``interval`` seconds without flushing, or

    2. The length of the queue has exceeded ``max_queue_length``, or

    3. A flush is triggered manually.

    :note: You should not need to instantiate, or even interact with, this
        yourself.  It is intended to be wrapped by ``lumberjack.Lumberjack``.
        If you do, for some reason, use this yourself, it is a subclass of
        ``threading.Thread``, so you should call its ``start()`` method after
        initialisation.

    :param elasticsearch: The ``elasticsearch.Elasticsearch`` object on which
        to perform the bulk indexing.

    :param config: The Lumberjack config.  See the Configuration section in the
        docs for details.

    """

    def __init__(self, elasticsearch, config):
        """Init method.  See class docstring."""
        super(ActionQueue, self).__init__()

        self.elasticsearch = elasticsearch
        self.config = config

        self.queue = []
        self._flush_event = Event()
        self.queue_lock = Lock()
        self.exceptions = []
        self.running = True
        self.logger = logging.getLogger(__name__)

        self.daemon = True
        # So we can monkey-patch these in testing
        self._bulk = bulk
        self._open = open

    @property
    def last_exception(self):
        """The last exception raised in the ActionQueue thread."""
        if len(self.exceptions) == 0:
            return None
        else:
            return self.exceptions[-1]

    def _run_postprocessors(self, queue_item):
        action, postprocessors = queue_item
        for postprocessor in postprocessors:
            try:
                action['_source'] = postprocessor(deepcopy(action['_source']))
            except Exception:
                self.logger.error('Postprocessor %s raised an exception.' %
                                  repr(postprocessor), exc_info=True)
        return action

    def _flush(self):
        """Perform all actions in the queue.

        Uses elasticsearch.helpers.bulk, and empties the queue on success.
        Uses the ``self.queue_lock`` to prevent a race condition.

        """
        with self.queue_lock:
            queue = list(self.queue)
            self.queue = []

        actions = map(self._run_postprocessors, queue)

        try:
            self._bulk(self.elasticsearch, actions)
        except TransportError:
            self.logger.error('Error in flushing queue. Falling back to file.',
                              exc_info=True)
            try:
                with self._open(self.config['fallback_log_file'],
                                'a') as log_file:
                    json_lines = map(lambda doc: dumps(doc) + '\n', actions)
                    for line in json_lines:
                        log_file.write(line)
            except IOError:
                self.logger.error('Error in fallback log. Lost %d logs.',
                                  len(actions), exc_info=True)
        else:
            self.logger.debug('Flushed %d logs into Elasticsearch.',
                              len(actions))

    def run(self):
        """The main method for the ActionQueue thread.

        Called by the ``start()`` method.  Not to be called directly.

        """
        while (self.running or len(self.queue) > 0):
            try:
                self._flush()
            except Exception as exc:
                self.logger.error(
                    'Unexpected exception in actions thread. ' +
                    'Continuing anyway.',
                    exc_info=True)
                self.exceptions.append(exc)
            finally:
                self._flush_event.clear()
                interval = self.config['interval']
                try:
                    triggered = self._flush_event.wait(interval)
                # Catch a weird bug in Python threading.  See tests.
                except TypeError:
                    self.logger.debug('Caught TypeError from Event.wait().  ' +
                                      'This is expected only during ' +
                                      'interpreter shutdown.', exc_info=True)
                    return
                if triggered:
                    self.logger.debug('Flushing on external trigger.')
                else:
                    self.logger.debug(
                        'Flushing after timeout of %.1fs.', interval)

    # These two methods to be called externally, i.e. from the main thread.
    # TODO: Consider refactoring.

    def trigger_flush(self):
        """Manually trigger a flush of the queue.

        This is to be called from the main thread, and fires an interrupt in
        the timeout of the main loop.  As such it is not guaranteed to
        immediately trigger a flush, only to skip the countdown to the next
        one.  This means the flush will happen the next time this thread gets
        switched to by the Python interpreter.

        """
        self.logger.debug('Flush triggered; setting event object.')
        self._flush_event.set()

    def queue_index(self, suffix, doc_type, body, postprocessors=None):
        """Queue a new document to be added to Elasticsearch.

        If the queue becomes longer than self.max_queue_length then a flush is
        automatically triggered.

        :param suffix: The suffix of the index into which we should index the
            document.

        :param doc_type: The Elasticsearch type of the document to be indexed.
            Usually this should correspond to a registered schema in
            Lumberjack.

        :param body: The actual document contents, as a dict.

        :param postprocessors: Any post-processing functions to be run on the
            document before indexing.

        """
        postprocessors = postprocessors if postprocessors is not None else []

        action = {
            '_op_type': 'index',
            '_index': self.config['index_prefix'] + suffix,
            '_type': doc_type,
            '_source': body
        }

        with self.queue_lock:
            self.queue.append((action, postprocessors))

        self.logger.debug(
            'Put an action in the queue. qlen = %d, doc_type = %s',
            len(self.queue), doc_type)

        # TODO: do default schema

        if self.config['max_queue_length'] is not None and \
                len(self.queue) >= self.config['max_queue_length']:
            self.logger.debug('Hit max_queue_length.')
            self.trigger_flush()
