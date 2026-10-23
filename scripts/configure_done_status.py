"""One-time host migration: closed DONE status and transitions for the existing runtime role."""
import json
from pathlib import Path
import subprocess


def main():
    code='''
role = ProjectRole.find_by!(name: 'AI Delivery Runtime')
status = Status.where('lower(name) = ?', 'done').first
status ||= Status.create!(name: 'DONE', is_closed: true, position: Status.maximum(:position).to_i + 1)
raise 'DONE must be closed' unless status.is_closed?
sources = Status.where('lower(name) IN (?)', %w[new analysis plan_review in_progress blocked failed cancelled done])
([Status.find(1)] + sources.to_a).uniq.each do |source|
  [1,3,5,6].each do |type_id|
    Workflow.find_or_create_by!(role: role, old_status: source, new_status: status, type_variant: Type.find(type_id).default_variant, author: false, assignee: false)
  end
end
puts "DONE_ID=#{status.id}"
'''
    result=subprocess.run(['docker','compose','run','--rm','--no-deps','-T','--entrypoint','bundle','openproject','exec','rails','runner','-'],input=code,text=True,capture_output=True,timeout=180)
    if result.returncode:
        Path('.local/done-status-migration.log').write_text(result.stdout+'\n'+result.stderr)
        raise RuntimeError('DONE status migration failed; see private operator log')
    value=next(line.split('=',1)[1] for line in result.stdout.splitlines() if line.startswith('DONE_ID='))
    path=Path('.env');lines=path.read_text().splitlines();found=False
    for i,line in enumerate(lines):
        if line.startswith('OPENPROJECT_STATUS_IDS='):
            mapping=json.loads(line.split('=',1)[1]);mapping['DONE']=int(value)
            lines[i]='OPENPROJECT_STATUS_IDS='+json.dumps(mapping,separators=(',',':'));found=True
    if not found:raise RuntimeError('Status mapping must already exist')
    path.write_text('\n'.join(lines)+'\n');path.chmod(0o600)
    print('Closed DONE status configured for existing AI Delivery Runtime role; no permissions added.')

if __name__=='__main__':main()
