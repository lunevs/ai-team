"""Операторский bootstrap явно согласованных неадминистративных runtime-доступов."""
import json
from pathlib import Path
import secrets
import subprocess

path=Path('.env')
settings=dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))

def save(values):
    with path.open('a') as stream:
        for key,value in values.items():
            if key not in settings:
                stream.write(f'{key}={value}\n'); settings[key]=value
    path.chmod(0o600)

def run(service,args,code):
    result=subprocess.run(['docker','compose','exec','-T',service,*args],input=code,text=True,capture_output=True,timeout=180)
    if result.returncode:
        print(result.stderr[:1600]); raise SystemExit(service+' bootstrap failed')
    for line in result.stdout.splitlines():
        if line.startswith('RUNTIME_JSON='): return json.loads(line.split('=',1)[1])
    raise SystemExit(service+' bootstrap returned no result')

save({'APP_RUNTIME_DB_PASSWORD':secrets.token_hex(24),'APP_API_TOKEN':secrets.token_hex(32)})
password=settings['APP_RUNTIME_DB_PASSWORD']
if not all(c in '0123456789abcdef' for c in password): raise SystemExit('Invalid password format')
queries=[('postgres',f"""DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='ai_team_runtime') THEN CREATE ROLE ai_team_runtime LOGIN PASSWORD '{password}'; END IF; END $$;
GRANT CONNECT ON DATABASE ai_team TO ai_team_runtime;"""),('ai_team',"""REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ai_team_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ai_team IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO ai_team_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ai_team IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO ai_team_runtime;""")]
for database,query in queries:
    proc=subprocess.run(['docker','compose','exec','-T','postgres','psql','-U','postgres','-d',database,'-v','ON_ERROR_STOP=1'],input=query,text=True,capture_output=True)
    if proc.returncode: raise SystemExit('Database grants failed')
print('Runtime DB role configured (no DDL/admin privileges).')

if 'OPENPROJECT_PM_TOKEN' not in settings or 'OPENPROJECT_SA_TOKEN' not in settings:
    save(run('openproject',['bundle','exec','rails','runner','-'],'''
require 'securerandom'
project = Project.find_by!(identifier: 'ai-team')
role = ProjectRole.find_or_initialize_by(name: 'AI Delivery Runtime')
role.permissions = [:view_project, :view_work_packages, :add_work_packages, :edit_work_packages, :add_work_package_comments, :view_members, :work_package_assigned, :manage_subtasks, :manage_work_package_relations]
role.save!
ids = {}; tokens = {}
%w[pm system_analyst financial_analyst data_developer application_developer designer qa].each do |name|
  user = User.find_or_initialize_by(login: 'agent_' + name)
  if user.new_record?
    user.firstname = 'Agent'; user.lastname = name; user.mail = name + '@agents.local.test'
    pass = 'Aa1!' + SecureRandom.hex(24); user.password = pass; user.password_confirmation = pass
    user.force_password_change = false; user.admin = false; user.status = 1; user.save!
  end
  member = Member.find_or_initialize_by(project: project, user_id: user.id)
  member.roles = [role]; member.save!; ids[name] = user.id
  if %w[pm system_analyst].include?(name)
    Token::API.where(user: user).select { |t| t.token_name == 'ai-team stage 1 runtime' }.each(&:destroy!)
    token = Token::API.create!(user: user, token_name: 'ai-team stage 1 runtime'); tokens[name] = token.plain_value
  end
end
statuses = {}
%w[NEW ANALYSIS PLAN_REVIEW IN_PROGRESS BLOCKED FAILED CANCELLED].each do |name|
  status = Status.where('lower(name) = ?', name.downcase).first
  status ||= Status.create!(name: name, is_closed: name == 'CANCELLED', position: Status.maximum(:position).to_i + 1)
  statuses[name] = status.id
end
([Status.find(1)] + Status.where(id: statuses.values)).each do |source|
  Status.where(id: statuses.values).each do |target|
    [1,3,5,6].each do |type_id|
      Workflow.find_or_create_by!(role: role, old_status: source, new_status: target, type_variant: Type.find(type_id).default_variant, author: false, assignee: false)
    end
  end
end
puts 'RUNTIME_JSON=' + {OPENPROJECT_PM_TOKEN: tokens['pm'], OPENPROJECT_SA_TOKEN: tokens['system_analyst'], AGENT_USER_IDS: ids.to_json, OPENPROJECT_STATUS_IDS: statuses.to_json}.to_json
'''))
    print('Seven project members and PM/SA API tokens configured (non-admin).')
else:
    # Existing-role repair avoids loading a second full Rails process under the local RAM cap.
    query="""INSERT INTO role_permissions(role_id,permission,created_at,updated_at)
      SELECT r.id,permission_name,now(),now() FROM roles r
      CROSS JOIN (VALUES ('work_package_assigned'),('manage_subtasks'),('manage_work_package_relations')) AS required(permission_name)
      WHERE r.name='AI Delivery Runtime' AND r.type='ProjectRole'
      AND NOT EXISTS(SELECT 1 FROM role_permissions p WHERE p.role_id=r.id AND p.permission=required.permission_name);"""
    result=subprocess.run(['docker','compose','exec','-T','postgres','psql','-U','postgres','-d','openproject','-v','ON_ERROR_STOP=1'],input=query,text=True,capture_output=True)
    if result.returncode:raise SystemExit('Existing runtime assignment repair failed')
    print('Existing runtime members may be assigned to project tasks; credentials retained.')

if 'BOOKSTACK_RUNTIME_TOKEN_ID' not in settings:
    save(run('bookstack',['php'],r'''<?php
chdir('/app/www'); require 'vendor/autoload.php';
$app = require 'bootstrap/app.php'; $app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
$role = BookStack\Users\Models\Role::firstOrNew(['display_name'=>'AI Documentation Runtime']);
$role->description = 'Read books, create/update pages and use API; no delete or account administration'; $role->save();
$names = ['book-view-all','page-view-all','page-create-all','page-update-all','access-api'];
$permissions = BookStack\Permissions\Models\RolePermission::whereIn('name',$names)->pluck('id');
if(count($permissions)!==count($names)) { throw new Exception('Missing runtime permission names'); }
$role->permissions()->sync($permissions);
$user = BookStack\Users\Models\User::firstOrNew(['email'=>'docs@agents.local.test']); $user->name = 'AI Documentation';
if(!$user->exists) { $user->password = Illuminate\Support\Facades\Hash::make(bin2hex(random_bytes(32))); }
$user->email_confirmed = true; $user->save(); $user->roles()->sync([$role->id]);
$secret = bin2hex(random_bytes(32)); $token = new BookStack\Api\ApiToken(); $token->user_id=$user->id;
$token->name='stage 1 runtime'; $token->token_id=bin2hex(random_bytes(16));
$token->secret=Illuminate\Support\Facades\Hash::make($secret); $token->expires_at=now()->addDays(30); $token->save();
$app->make(BookStack\Permissions\JointPermissionBuilder::class)->rebuildForAll();
echo 'RUNTIME_JSON='.json_encode(['BOOKSTACK_RUNTIME_TOKEN_ID'=>$token->token_id,'BOOKSTACK_RUNTIME_TOKEN_SECRET'=>$secret]).PHP_EOL;
'''))
    print('BookStack non-admin runtime user and 30-day token configured.')
