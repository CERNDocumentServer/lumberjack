# -*- coding: utf-8 -*-
##
## This file is part of ESLog.
## Copyright (C) 2014 CERN.
##
## Invenio is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 2 of the
## License, or (at your option) any later version.
##
## Invenio is distributed in the hope that it will be useful, but
## WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
## General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Invenio; if not, write to the Free Software Foundation, Inc.,
## 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.

from __future__ import absolute_import
import unittest

import eslog

import logging
import elasticsearch
import sys
import time
from random import randint

from .common import HOSTS, INDEX_PREFIX, ES_LOGLEVEL

SCHEMA_A = {
    'dynamic': 'strict',
    '_source': {'enabled': True},
    'properties': {
        'a': {
            'index': 'analyzed',
            'type': 'string',
            'fields': {
                'raw': {
                    'index': 'not_analyzed',
                    'type': 'string'
                }
            }
        },
        'b': {
            'type': 'long'
        }
    }
}

class SchemaTestCase(unittest.TestCase):
    def setUp(self):
        self.index_infix = str(randint(0, 2**30)) + '-'
        self.esl = eslog.ESLog(hosts=HOSTS,
                               index_prefix=INDEX_PREFIX + self.index_infix)
        self.es = self.esl.context.elasticsearch
        
        l = logging.getLogger('elasticsearch')
        l.setLevel(ES_LOGLEVEL)
        l.addHandler(logging.StreamHandler(stream=sys.stderr))

    def tearDown(self):
        self.esl.context.elasticsearch.indices.delete(
            index=INDEX_PREFIX + self.index_infix + '*')

    def test_build_mappings_a(self):
        self.esl.context.schemas['type_a'] = SCHEMA_A
        expected_mapping_a = {
            'dynamic': 'strict',
            '_source': {'enabled': True},
            '_all': {'enabled': False},
            '_ttl': {'enabled': True},
            'properties': {
                'message': {
                    'type': 'string',
                    'index': 'not_analyzed',
                    'norms': {'enabled': False}
                },
                '@timestamp': {
                    'type': 'date',
                    'format': 'dateOptionalTime',
                },
                'level': {
                    'type': 'integer'
                },
                'a': {
                    'type': 'string',
                    'index': 'analyzed',
                    'fields': {
                        'raw': {
                            'index': 'not_analyzed',
                            'type': 'string'
                        }
                    },
                    'norms': {'enabled': False}
                },
                'b': {
                    'type': 'long'
                }
            }
        }
        assert self.esl.context._build_mappings()['type_a'] == expected_mapping_a

    def test_register_schema(self):
        self.esl.register_schema('type_a', SCHEMA_A)

        # Test it's now in ES.
        res = self.es.indices.get_template(
            name=INDEX_PREFIX + self.index_infix + '*')

        expected_schema = self.esl.context._build_mappings()['type_a']
                
        assert res[INDEX_PREFIX + self.index_infix + '*'] \
            ['mappings']['type_a'] == expected_schema

def suite():
    suite = unittest.makeSuite(SchemaTestCase, 'test')
    return suite