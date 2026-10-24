"""Disposable completed experiments only; no credentials, provider calls or live reviews."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import test_permutation as fixtures
from test_permutation import Fake
from renderrepo.permutation import engine as e
from renderrepo.permutation.comparison import server


class ComparisonTests(unittest.TestCase):
    setUp = fixtures.PermutationTests.setUp
    plan = fixtures.PermutationTests.plan
    ready = fixtures.PermutationTests.ready

    def start(self):
        http=server(self.manifest,0)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        return http, f'http://127.0.0.1:{http.server_port}'

    def fixture(self):
        self.spec['fixed']['cfg']=1.0
        old,_=self.ready();e.run(old,Fake(),sleep=lambda _:None)
        for row in e.results(old):e.review(old,row['id'],'modify','Baseline remains unchanged')
        self.spec['fixed']['cfg']=3.0
        new,_=self.ready();e.run(new,Fake(),sleep=lambda _:None)
        self.baseline=old;self.new=new
        self.protected={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in old.rglob('*') if p.is_file()}
        candidates=[]
        for directory,editable in ((old,False),(new,True)):
            for row in e.results(directory):
                candidates.append({'source_experiment':directory.name,'source_directory':str(directory),'new_submission_required':editable,'record':row,'kind':'new result' if editable else 'reused baseline'})
        self.manifest=self.root/'comparison.json'
        e.atomic_json(self.manifest,{'status':'Completed comparison','candidates':candidates})

    def test_real_click_roundtrip_change_reopen_and_exports(self):
        self.fixture();http,base=self.start()
        result=subprocess.run(['node','--test','tests/test_comparison_frontend.cjs'],cwd=Path(__file__).resolve().parents[1],env={**os.environ,'RR_COMPARISON_URL':base},capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        rows=e.results(self.new)
        self.assertEqual([r['review']['rating'] for r in rows],['toss','toss'])
        self.assertEqual([r['review']['notes'] for r in rows],['final notes 0','final notes 1'])
        _,reopened=self.start()
        with urlopen(reopened+'/data') as response:data=json.load(response)
        live=[c for c in data['candidates'] if c['editable']]
        self.assertEqual([c['record']['review'] for c in live],[r['review'] for r in rows])
        exported=e.read(self.manifest)
        with (self.root/'comparison.csv').open(newline='') as f:csvrows=list(csv.DictReader(f))
        for c,line in zip(exported['candidates'],csvrows):
            self.assertEqual(c['record']['review']['rating'],line['rating'])
            self.assertEqual(c['record']['review']['notes'],json.loads(line['notes_json']))
            self.assertEqual(c,json.loads(line['provenance_json']))
        self.assertEqual([r['review'] for r in e.read(self.root/'new-results-export/results.json')['results']],[r['review'] for r in rows])
        for p,sha in self.protected.items():self.assertEqual(hashlib.sha256(Path(p).read_bytes()).hexdigest(),sha)

    def test_baseline_and_wrong_experiment_rejected_and_export_failure_is_explicit(self):
        self.fixture();http,base=self.start()
        with urlopen(base+'/data') as response:token=json.load(response)['token']
        def post(experiment,job):
            request=Request(base+'/review',json.dumps({'experiment':experiment,'job':job,'rating':'keeper','notes':'new'}).encode(),headers={'Origin':base,'X-RR-Review':token,'Content-Type':'application/json'})
            try:
                with urlopen(request) as response:return response.status,json.load(response)
            except HTTPError as error:return error.code,json.load(error)
        baseline=e.results(self.baseline)[0]
        self.assertEqual(post(self.baseline.name,baseline['id'])[0],400)
        row=e.results(self.new)[0]
        self.assertEqual(post(self.baseline.name,row['id'])[0],400)
        with patch.object(http.comparison,'export',side_effect=OSError('disk error')):
            code,result=post(self.new.name,row['id'])
        self.assertEqual(code,500);self.assertTrue(result['saved']);self.assertIn('export',result['error'])
        self.assertEqual(e.results(self.new)[0]['review']['rating'],'keeper')
        for p,sha in self.protected.items():self.assertEqual(hashlib.sha256(Path(p).read_bytes()).hexdigest(),sha)
