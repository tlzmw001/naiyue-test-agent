"""Bounded archive-to-model projection, preserving exact Unicode source spans."""
import json
import math

from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent.contracts.memory import MemoryReadError
from .memory_sources import SECRET

VISIBLE = ('unit_id','evidence_id','turn_key','message_key','role','text','origin','context_only',
    'partial_before','partial_after','occurred_at','user_origin','tool_pair_status','reply_id',
    'parent_message_id','tool_call_id','name','tool_status')


def model_batch(batch):
    return dict(units=[{k:u[k] for k in VISIBLE} for u in batch.get('units',[])],
        complete=batch.get('complete',False),skipped=batch.get('skipped',[]),coverage=batch.get('coverage',{}))


def tokens(batch):
    return count_tokens_approximately([HumanMessage(content=json.dumps(model_batch(batch),ensure_ascii=False))])


def spans(text, start=0, *, final=True):
    """Natural spans keep punctuation; hard-cut tails remain noncitable until a boundary."""
    beginning, quote, brackets, fence = 0, None, [], False
    pieces=[]
    fence_ticks=set()
    for i,char in enumerate(text):
        if i in fence_ticks:continue
        if text.startswith('```',i):
            fence=not fence;fence_ticks.update((i+1,i+2));continue
        if not fence:
            if quote:
                if char==quote:quote=None
            elif char in ('"','“','‘','`'):
                quote={'“':'”','‘':'’'}.get(char,char)
            elif char in '([{（【':brackets.append(char)
            elif char in ')]}）】' and brackets:brackets.pop()
        boundary = char=='\n' and quote is None and (not brackets or fence) or (not fence and quote is None and not brackets and
            (char in '。！？!?；;' or char=='.' and (i+1==len(text) or text[i+1].isspace())))
        if boundary:
            pieces.extend(_piece(text,beginning,i+1,start,True))
            beginning=i+1
    if beginning<len(text):pieces.extend(_piece(text,beginning,len(text),start,final and quote is None and not brackets and not fence))
    return pieces


def _piece(text,lo,hi,start,complete):
    safe=complete and hi-lo<=300
    return [(start+i,text[i:min(i+300,hi)],safe) for i in range(lo,hi,300)]


def assemble(archive, *, session_id,thread_id,conversation_revision,start,upper,token_budget,
        source_chars,max_messages,max_read_chars,overlap_chars,context_floor):
    saved=archive.parent(session_id)
    if saved is None or saved['thread_id']!=thread_id or saved['conversation_revision']!=conversation_revision:
        raise MemoryReadError('memory_source_changed')
    if saved['project_id'] is None or saved['project_id']!=archive.binding():
        raise MemoryReadError('memory_source_scope_mismatch')
    position=[max(1,start[0]),start[1]]
    batch=dict(units=[],evidence_map={},next_position=position,complete=position[0]>upper,
        history_version=archive.history_version(session_id,thread_id),skipped=[],context_char_limit=overlap_chars,
        coverage=dict(read_ranges=[],omitted_ranges=[]))
    if tokens(batch)>token_budget:raise MemoryReadError('memory_batch_budget')
    if position[0]>upper:return batch
    row_limit=max(1,max_messages//3)
    page=archive.metadata_page(session_id,thread_id,start=position[0],upper=upper,limit=row_limit)
    if overlap_chars and position[0]>context_floor and len(page['rows'])<row_limit:
        prior=archive.metadata_page(session_id,thread_id,start=max(context_floor,position[0]-max_messages),
            upper=position[0]-1,limit=row_limit-len(page['rows']))
        page['rows']=prior['rows']+page['rows']
    relationship_budget=max_messages-len(page['rows'])
    read_chars=0;overlap_used=0
    prepared=[]
    for row in page['rows']:
        seq,total=row['sequence'],row['text_chars']
        overlap=seq<position[0]
        offset=position[1] if seq==position[0] else 0
        if offset>total:raise MemoryReadError('memory_batch_position_invalid')
        bounds=dict(turn_key=f'unknown:{seq}',start_known=False)
        if relationship_budget:
            bounds=archive.turn_bounds(session_id,thread_id,sequence=seq,upper=upper)
            relationship_budget-=1
        if overlap:
            if not relationship_budget:continue
            current=archive.turn_bounds(session_id,thread_id,sequence=position[0],upper=upper)
            relationship_budget-=1
            if overlap_used>=overlap_chars or bounds['turn_key']!=current['turn_key']:continue
            offset=max(0,total-(overlap_chars-overlap_used))
        reason=('sensitive' if row['sensitive'] or any(SECRET.search(str(row.get(k) or ''))
            for k in ('name','tool_call_id','parent_message_id','reply_id')) else 'time_unavailable' if row['occurred_at'] is None
            or not math.isfinite(row['occurred_at']) else 'empty' if offset==total else None)
        if reason:
            if not overlap:prepared.append((bounds,[],[seq+1,0],[(seq,offset,total,reason)]))
            continue
        prior_text=''
        prior_start=max(0,offset-(overlap_chars-overlap_used))
        if not overlap and seq==position[0] and offset and overlap_chars:
            count=min(offset-prior_start,max_read_chars-read_chars)
            prior_text=archive.visible(session_id,thread_id,seq,offset=prior_start,chars=count)['content']
            read_chars+=len(prior_text);overlap_used+=len(prior_text)
        allowed=min(total-offset,max_read_chars-read_chars,overlap_chars-overlap_used if overlap else max_read_chars)
        if allowed<=0:break
        chunks=[];consumed=0
        while consumed<allowed:
            data=archive.visible(session_id,thread_id,seq,offset=offset+consumed,
                chars=min(source_chars,allowed-consumed))
            if not data or not data['content']:raise MemoryReadError('memory_source_changed')
            chunks.append(data['content']);consumed+=len(data['content'])
        text=''.join(chunks);read_chars+=len(text)
        if overlap:overlap_used+=len(text)
        link=None
        if row['message_type']=='tool' and relationship_budget:
            relationship_budget-=1
            link=archive.tool_link(session_id,thread_id,parent_message_id=row['parent_message_id'],
                tool_call_id=row['tool_call_id'],before_sequence=seq)
        pair=('not_tool' if row['message_type']!='tool' else 'missing' if link is None else
            'paired' if link['status']=='paired' and link['name']==row['name'] and link['reply_id']==row['reply_id'] else 'invalid')
        units=[]
        if pair=='paired':
            units.append(dict(sequence=seq,session_id=session_id,thread_id=thread_id,message_id=row['parent_message_id'],
                revision=row['revision'],conversation_revision=conversation_revision,turn_key=bounds['turn_key'],
                message_key=row['parent_message_id'],role='ai',reply_id=row['reply_id'],parent_message_id=None,
                tool_call_id=row['tool_call_id'],name=row['name'],tool_status=None,occurred_at=link['occurred_at'],
                user_origin=False,text='',start_offset=0,end_offset=0,text_chars=0,origin='overlap' if overlap else 'new',
                context_only=True,partial_before=False,partial_after=False,tool_pair_status='paired'))
        scanned=spans(prior_text+text,prior_start,final=offset+len(text)==total) if offset and prior_start==0 and prior_text else spans(text,offset,final=offset+len(text)==total)
        for at,part,safe in scanned:
            if at+len(part)<=offset:continue
            if at<offset:
                part=part[offset-at:];at=offset;safe=False
            if offset and (prior_start!=0 or not prior_text):safe=False
            # Starting within an old sentence cannot turn its suffix into independent evidence.
            if offset>0 and at==offset and (not prior_text or prior_text[-1] not in '。！？!?；;\n'):safe=False
            citable=safe and pair not in ('missing','invalid')
            units.append(dict(sequence=seq,session_id=session_id,thread_id=thread_id,message_id=row['message_id'],
                revision=row['revision'],conversation_revision=conversation_revision,turn_key=bounds['turn_key'],
                message_key=row['message_id'],role=row['message_type'],reply_id=row['reply_id'],
                parent_message_id=row['parent_message_id'],tool_call_id=row['tool_call_id'],name=row['name'],
                tool_status=row['tool_status'],occurred_at=row['occurred_at'],user_origin=row['message_type']=='human' and bool(row['user_origin']),
                text=part,start_offset=at,end_offset=at+len(part),text_chars=total,origin='overlap' if overlap else 'new',
                context_only=not citable,partial_before=at>0,partial_after=at+len(part)<total,tool_pair_status=pair))
        if prior_text:
            originals=[]
            for at,part,safe in spans(prior_text,prior_start,final=prior_text[-1] in '。！？!?；;\n'):
                if prior_start and at==prior_start:safe=False
                original=dict(units[-1],text=part,start_offset=at,end_offset=at+len(part),origin='overlap',
                    partial_before=at>0,partial_after=True,context_only=not safe or pair in ('missing','invalid') or prior_start>0)
                originals.append(original)
            units=originals+units
        prepared.append((bounds,units,[seq+1,0] if offset+len(text)==total else [seq,offset+len(text)],[]))
    groups=[]
    for bounds,units,end,omitted in prepared:
        if groups and groups[-1][0]['turn_key']==bounds['turn_key'] and bounds['start_known']:
            groups[-1][1].extend(units);groups[-1][2][:]=end;groups[-1][3].extend(omitted)
        else:groups.append([bounds,units,end,omitted])
    for bounds,units,end,omitted in groups:
        candidate=_extend(batch,units,end,omitted,upper)
        if tokens(candidate)<=token_budget:
            batch=candidate
            continue
        if any(u['origin']=='new' and u['text'] for u in batch['units']):break
        if omitted:batch=_extend(batch,[],batch['next_position'],omitted,upper)
        # An oversized interaction falls back to complete messages, then natural units.
        messages=[]
        for unit in units:
            if messages and messages[-1][-1]['sequence']==unit['sequence']:
                messages[-1].append(unit)
            else:messages.append([unit])
        for message_units in messages:
            last=message_units[-1]
            message_end=([last['sequence']+1,0] if last['end_offset']==last['text_chars']
                else [last['sequence'],last['end_offset']])
            whole=_extend(batch,message_units,message_end,[],upper)
            if tokens(whole)<=token_budget:
                batch=whole;continue
            if any(u['origin']=='new' and u['text'] for u in batch['units']):return batch
            for unit in message_units:
                end_unit=[unit['sequence'],unit['end_offset']]
                if unit['end_offset']==unit['text_chars']:end_unit=[unit['sequence']+1,0]
                candidate=_extend(batch,[unit],end_unit,[],upper)
                if tokens(candidate)>token_budget and unit['origin']=='overlap':continue
                if tokens(candidate)>token_budget and unit['origin']=='new' and any(u['origin']=='overlap' for u in batch['units']):
                    batch={**batch,'units':[u for u in batch['units'] if u['origin']=='new']}
                    candidate=_extend(batch,[unit],end_unit,[],upper)
                if tokens(candidate)>token_budget:
                    if any(u['origin']=='new' and u['text'] for u in batch['units']):return batch
                    return _extend(batch,[],[unit['sequence']+1,0],[(unit['sequence'],unit['start_offset'],unit['text_chars'],'envelope_too_large')],upper)
                batch=candidate
            break
        break
    if not page['more'] and (not page['rows'] or batch['next_position']==[page['rows'][-1]['sequence']+1,0]):
        batch['next_position']=[upper+1,0];batch['complete']=True
    if archive.history_version(session_id,thread_id)!=batch['history_version']:
        raise MemoryReadError('memory_source_changed')
    return batch


def _extend(batch,units,end,omitted,upper):
    result={**batch,'units':[*batch['units'],*(dict(u) for u in units)],'evidence_map':{},
        'skipped':[*batch['skipped'],*(f'{r[3]}:{r[0]}' for r in omitted)],
        'coverage':{k:list(v) for k,v in batch['coverage'].items()}}
    result['coverage']['omitted_ranges'].extend(omitted)
    for i,u in enumerate(result['units'],1):
        u['unit_id']=f'U{i}';u['evidence_id']=None
        if not u['context_only']:
            u['evidence_id']=f'E{len(result["evidence_map"])+1}'
            result['evidence_map'][u['evidence_id']]=u
    for u in units:
        if u['origin']=='new' and u['text']:
            result['coverage']['read_ranges'].append((u['sequence'],u['start_offset'],u['end_offset']))
    if any(u['origin']=='new' and u['text'] for u in units) or omitted:
        result['next_position']=end;result['complete']=end[0]>upper
    return result
