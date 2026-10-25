"""Structured scene failures, bounded recovery and navigation isolation."""
import struct
from unittest.mock import Mock
import pytest
from tests.unit.test_mono_scene import dictionary
from utils.drivers.mono_scene import SceneMetadata
from utils.ui.scene_map_worker import SceneMapWorker


def test_mutation_has_structured_cause_and_stage_without_weakening_checks():
    r, obj = dictionary()
    entries = struct.unpack('<Q', r.read(obj+24, 8))[0]
    def read(at, size):
        data = r.read(at, size)
        if at == entries+32:
            r.write(obj+76, struct.pack('<i', 1))
        return data
    meta = SceneMetadata(read, r.domain)
    meta.stage = 'registry_end'
    with pytest.raises(ValueError) as caught:
        meta.dictionary(obj)
    issue = caught.value
    assert issue.kind == 'mutation'
    assert issue.stage == 'registry_end'
    assert issue.retryable and not issue.scope_verified


def test_invalid_reference_is_classified_but_never_declared_retryable():
    r, obj = dictionary()
    r.p64(obj+24, 1)
    meta = SceneMetadata(r.read, r.domain)
    meta.stage = 'entities'
    with pytest.raises(ValueError) as caught:
        meta.pointer(obj+24)
    assert caught.value.kind == 'invalid_reference'
    assert caught.value.stage == 'entities'
    assert not caught.value.retryable


def test_unknown_error_keeps_legacy_close_and_safe_diagnostics():
    reader = Mock()
    reader.snapshot.side_effect = ValueError('bad pointer 0x1234')
    worker = SceneMapWorker('profile', factory=lambda *a: reader)
    worker.set_pid(7)
    worker.poll_once()
    result = worker.take_latest()
    assert result['issue'] == {'kind': 'unknown', 'stage': 'snapshot', 'retryable': False,
                               'scope_verified': False}
    assert '0x' not in result['message'] and '0x' not in str(result['issue'])
    reader.close.assert_called_once()
