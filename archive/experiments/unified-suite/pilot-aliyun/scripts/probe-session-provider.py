#!/usr/bin/env python3
"""Check provider models, or explicitly request one tiny generation smoke test.

Credentials, URLs and response bodies are never written to the result. No retry
or redirect. A successful model list is not proof that generation will succeed.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


def streaming_generation(raw):
    """Read a bounded chat-completions SSE reply without retaining its text."""
    content=[];usage=None;model=None;done=False;events=0
    for line in raw.decode('utf-8').splitlines():
        if not line.startswith('data:'):continue
        value=line[5:].strip()
        if value=='[DONE]':done=True;continue
        message=json.loads(value);events+=1
        if message.get('model'):model=message['model']
        if message.get('usage'):usage=message['usage']
        for choice in message.get('choices',[]):
            content.append(choice.get('delta',{}).get('content') or '')
    return dict(response_model=model,usage=usage,done_marker=done,data_events=events,
        expected_content_matched=''.join(content).strip()=='OK')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--provider-config',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    generation=p.add_mutually_exclusive_group()
    generation.add_argument('--generation-smoke',action='store_true',help='One real request: original model name, thinking disabled, at most 16 output tokens')
    generation.add_argument('--stream-generation-smoke',action='store_true',help='One SSE request with the same 16-token limit; no native-session claim')
    a=p.parse_args()
    generate=a.generation_smoke or a.stream_generation_smoke
    if a.out.exists():raise ValueError('New provider probe result required')
    provider=json.loads(a.provider_config.read_text(encoding='utf-8-sig'))['provider']['deepseek']
    base=provider['options']['baseURL'];key=provider['options']['apiKey'];url=urllib.parse.urlsplit(base)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or not key or key.startswith('{'):
        raise ValueError('Original HTTPS provider and literal private credential required')
    spec=importlib.util.spec_from_file_location('provider_probe_relay',Path(__file__).with_name('session-relay.py'))
    relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)
    request=urllib.request.Request(base.rstrip('/')+'/models',headers={'Authorization':'Bearer '+key})
    if generate:
        raw=json.dumps(dict(model='deepseek-v4-flash',messages=[dict(role='user',content='Reply only OK.')],
            max_tokens=16,thinking=dict(type='disabled'),stream=a.stream_generation_smoke)).encode()
        request=urllib.request.Request(base.rstrip('/')+'/chat/completions',data=raw,
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    result=dict(at=datetime.now(timezone.utc).isoformat(),passed=False,model_generation_requests=int(generate),
        automatic_retry=False,redirects_allowed=False,request='POST tiny generation' if generate else 'GET configured provider /models',
        provider_config_sha256=hashlib.sha256(a.provider_config.read_bytes()).hexdigest(),
        scope='Provider transport/authentication/model-list probe only; generation and filesystem recovery remain separate')
    start=time.monotonic()
    try:
        try:response=urllib.request.build_opener(relay.NoRedirect()).open(request,timeout=15)
        except urllib.error.HTTPError as error:response=error
        with response:
            raw=response.read(1024*1024+1);result['http_status']=response.status
        if len(raw)>1024*1024:raise ValueError('Model list exceeds bounded probe size')
        result['response_bytes']=len(raw);result['response_sha256']=hashlib.sha256(raw).hexdigest()
        if a.stream_generation_smoke:
            value=streaming_generation(raw)
            result.update(value,requested_model='deepseek-v4-flash',thinking='disabled',max_output_tokens=16,
                stream=True,scope='One tiny SSE model health check; not OpenCode or filesystem recovery qualification')
            result['passed']=result['http_status']==200 and value['done_marker'] and value['expected_content_matched']
        elif a.generation_smoke:
            value=json.loads(raw)
            result.update(model_generation_requests=1,requested_model='deepseek-v4-flash',thinking='disabled',max_output_tokens=16,
                response_model=value.get('model'),usage=value.get('usage'),
                scope='One tiny non-thinking model health check; not OpenCode or filesystem recovery qualification')
            result['passed']=result['http_status']==200 and value.get('choices',[{}])[0].get('message',{}).get('content','').strip()=='OK'
        else:
            value=json.loads(raw)
            result['advertised_model_ids']=[row['id'] for row in value.get('data',[]) if isinstance(row.get('id'),str) and re.fullmatch(r'[a-zA-Z0-9/._-]{1,120}',row['id'])]
            result['configured_api_model_id']=provider['models']['deepseek-v4-flash'].get('id','deepseek-v4-flash')
            result['expected_model_advertised']='deepseek-v4-flash' in result['advertised_model_ids']
            result['passed']=result['http_status']==200
    except Exception as error:result['error_type']=type(error).__name__
    result['seconds']=time.monotonic()-start
    with a.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
