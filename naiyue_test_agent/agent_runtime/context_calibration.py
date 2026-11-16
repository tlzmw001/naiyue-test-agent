"""Pure request-level calibration; persistence and model execution belong to callers."""
from copy import deepcopy
from fractions import Fraction
from ..contracts.context_calibration import BudgetEstimate, RequestMeasurement
import json
import logging
import re

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.messages.utils import count_tokens_approximately
from ..model_access.calibration_identity import KEY as IDENTITY_KEY, fingerprint
from ..model_access.usage import normalize_model_usage

KEY = 'naiyue_token_calibration'
logger = logging.getLogger(__name__)


def _hex(value, size=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % size, value) is not None


def _positive(value):
    return type(value) is int and value > 0


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def read_calibration(value):
    """Read a bounded JSON record; never expose provider content in diagnostics."""
    try:
        if not isinstance(value, dict) or len(_encoded(value).encode()) > 16384:
            raise ValueError('record')
        if set(value) != {'version','identity','revision','source','agent','summary'}:
            raise ValueError('fields')
        if type(value['version']) is not int or value['version'] != 1:
            raise ValueError('version')
        if not _hex(value['identity']) or not _positive(value['revision']):
            raise ValueError('header')
        if value['source'] not in ('response','summary'):
            raise ValueError('source')
        seen=set()
        for purpose in ('agent','summary'):
            state=value[purpose]
            if not isinstance(state,dict) or set(state)!={'reported_model_hash','text','image'}:
                raise ValueError('purpose')
            if state['reported_model_hash'] is not None and not _hex(state['reported_model_hash']):
                raise ValueError('model')
            for media in ('text','image'):
                sample=state[media]
                if sample is None:
                    continue
                if not isinstance(sample,dict) or set(sample)!={'sample_id','raw_input_tokens','actual_input_tokens'}:
                    raise ValueError('sample')
                if not _hex(sample['sample_id'],32) or sample['sample_id'] in seen:
                    raise ValueError('sample_id')
                seen.add(sample['sample_id'])
                if not all(_positive(sample[k]) for k in ('raw_input_tokens','actual_input_tokens')):
                    raise ValueError('count')
        return deepcopy(value)
    except (ValueError, TypeError, OverflowError, RecursionError):
        logger.warning('calibration_record_invalid')
        return None


def _select(history, identity):
    high=0; candidates=[]; other=False
    for message in history:
        value=message.response_metadata.get(KEY)
        if value is None:
            continue
        if not isinstance(value,dict) or not _hex(value.get('identity')) or not _positive(value.get('revision')):
            logger.warning('calibration_record_header_invalid')
            continue
        if value['identity'] != identity:
            other=True
            continue
        revision=value['revision']
        if revision>high:
            high=revision; candidates=[value]
        elif revision==high:
            candidates.append(value)
    if not candidates:
        return None,high,'identity_changed' if other else 'no_samples'
    records=[read_calibration(value) for value in candidates]
    if any(value is None for value in records) or any(value!=records[0] for value in records):
        logger.warning('calibration_latest_record_invalid')
        return None,high,'invalid_record'
    return records[0],high,None


def _shape(messages, tools, response_format):
    if response_format is not None:
        return False,'text'
    media='text'
    for message in messages:
        content=message.content
        if isinstance(content,str):
            continue
        if not isinstance(content,list):
            return False,media
        for block in content:
            if isinstance(block,str):
                continue
            if not isinstance(block,dict):
                return False,media
            kind=block.get('type')
            if kind in ('image','image_url','input_image'):
                media='image'
            elif kind in ('tool_use','function_call') and isinstance(message,AIMessage):
                continue
            elif kind not in ('text','input_text','output_text','thinking','reasoning','redacted_thinking'):
                return False,media
    for tool in tools:
        if isinstance(tool,dict) and tool.get('type') not in (None,'function'):
            return False,media
    return True,media


def measure_request(model, system_message, projected_messages, tools, *, purpose='agent',
                    response_format=None, calibration_history=None):
    if purpose not in ('agent','summary'):
        raise ValueError('Unsupported calibration purpose')
    messages=list(projected_messages)
    supported,media=_shape([*([system_message] if system_message else []),*messages],tools,response_format)
    identity_data=(model.metadata or {}).get(IDENTITY_KEY)
    try:
        identity=fingerprint(identity_data) if identity_data else None
    except (ValueError,TypeError):
        logger.warning('calibration_identity_invalid')
        identity=None
    source,high,reason=_select(messages if calibration_history is None else calibration_history,identity) if identity else (None,0,'identity_unavailable')
    try:
        raw=count_tokens_approximately([*([system_message] if system_message else []),*messages],tools=tools)
        if type(raw) is not int or raw<0:
            raise ValueError('invalid count')
    except Exception as error:
        logger.warning('calibration_raw_unavailable error_type=%s',type(error).__name__)
        return RequestMeasurement(purpose,media,identity,source,high,
            BudgetEstimate('unavailable',None,None,'unavailable','raw_estimate_unavailable'),False,None)
    eligible=bool(supported and identity and raw>0)
    ratio=Fraction(1);method='uncalibrated'
    if not supported:
        reason='unsupported_request_shape'
    elif identity is None:
        reason='identity_unavailable'
    elif source is not None:
        sample=source[purpose][media]
        if sample is None and purpose=='summary' and media=='text':
            sample=source['agent']['text']
            if sample is not None:
                method='inherited_agent_ratio'
        if sample is not None:
            ratio=Fraction(sample['actual_input_tokens'],sample['raw_input_tokens'])
            method='ratio_estimate' if method=='uncalibrated' else method
            reason=None
        else:
            reason='media_changed' if source[purpose]['image' if media=='text' else 'text'] else 'no_samples'
    calibrated=(raw*ratio.numerator+ratio.denominator-1)//ratio.denominator
    return RequestMeasurement(purpose,media,identity,source,high,
        BudgetEstimate('available',raw,calibrated,method,reason),eligible,ratio)


def _empty(identity,revision,source):
    return {'version':1,'identity':identity,'revision':revision,'source':source,
        **{purpose:{'reported_model_hash':None,'text':None,'image':None} for purpose in ('agent','summary')}}


def complete_sample(measurement,response,*,sample_id):
    if (not measurement.sampling_eligible or not isinstance(response,AIMessage)
            or isinstance(response,AIMessageChunk)):
        return None
    actual=normalize_model_usage(response.usage_metadata)['input_tokens']
    if not _positive(actual) or not _hex(sample_id,32):
        logger.warning('calibration_sample_unavailable')
        return None
    sample={'sample_id':sample_id,'raw_input_tokens':measurement.budget.raw_input_tokens,
            'actual_input_tokens':actual}
    record=deepcopy(measurement.source_envelope) if measurement.source_envelope else _empty(
        measurement.identity,measurement.revision_high_water+1,'response')
    for purpose in ('agent','summary'):
        for media in ('text','image'):
            old=record[purpose][media]
            if old and old['sample_id']==sample_id:
                if purpose==measurement.purpose and media==measurement.media and old==sample:
                    return record
                logger.warning('calibration_sample_conflict')
                return None
    state=record[measurement.purpose]
    name=response.response_metadata.get('model_name') or response.response_metadata.get('model')
    if isinstance(name,str) and name:
        reported=fingerprint(name)
        if state['reported_model_hash'] is not None and state['reported_model_hash']!=reported:
            state['text']=state['image']=None
        state['reported_model_hash']=reported
    state[measurement.media]=sample
    record.update(revision=measurement.revision_high_water+1,source='response')
    return read_calibration(record)


def carry_compaction_state(agent_measurement,summary_envelope):
    if agent_measurement.identity is None:
        return None
    summary=read_calibration(summary_envelope) if summary_envelope is not None else None
    if summary_envelope is not None and (summary is None or summary['identity']!=agent_measurement.identity):
        logger.warning('calibration_carry_invalid')
        return None
    base=summary if summary is not None else agent_measurement.source_envelope
    record=deepcopy(base) if base else _empty(agent_measurement.identity,1,'summary')
    record.update(source='summary',revision=max(agent_measurement.revision_high_water,
        record['revision'])+1)
    return read_calibration(record)
