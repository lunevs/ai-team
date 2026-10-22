from app.git_broker import evaluate_pull

def test_no_self_approval_and_no_stale_qa():
 sha='a'*40
 pr={'head':{'sha':sha},'merged':True,'user':{'login':'author'}}
 checks=[{'name':name,'status':'completed','conclusion':'success','id':i} for i,name in enumerate(['application','validate'])]
 assert not evaluate_pull(pr,[{'user':{'login':'author'},'state':'APPROVED'}],checks,{},sha)['eligible']
 review={'user':{'login':'reviewer'},'state':'APPROVED'}
 assert evaluate_pull(pr,[review],checks,{},sha)['eligible']
 assert not evaluate_pull(pr,[review],checks,{},'b'*40)['eligible']
 assert not evaluate_pull(pr,[review],[],{},sha)['eligible']
 changed=[review,{'id':2,'user':{'login':'reviewer'},'state':'CHANGES_REQUESTED'}]
 assert not evaluate_pull(pr,changed,checks,{},sha)['eligible']
