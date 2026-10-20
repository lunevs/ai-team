"""Локальный admin bootstrap. Секреты сохраняются в .env, stdout не содержит их."""
import json
from pathlib import Path
import subprocess

path = Path('.env')
existing = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))

def run(service, command, code):
    proc = subprocess.run(['docker', 'compose', 'exec', '-T', service, *command], input=code, text=True, capture_output=True, timeout=180)
    if proc.returncode:
        # Bootstrap code never prints secret values before success.
        print(proc.stderr[:1800])
        raise SystemExit(f'{service}: bootstrap failed ({proc.returncode})')
    for line in proc.stdout.splitlines():
        if line.startswith('BOOTSTRAP_JSON='):
            return json.loads(line.split('=', 1)[1])
    raise SystemExit(f'{service}: no bootstrap result')

def save(values):
    with path.open('a') as stream:
        for key, value in values.items():
            stream.write(f'{key}={value}\n')
    path.chmod(0o600)

if 'OPENPROJECT_API_KEY' not in existing:
    data = run('openproject', ['bundle', 'exec', 'rails', 'runner', '-'], '''
require 'securerandom'
user = User.find_by!(login: 'admin')
password = 'Aa1!' + SecureRandom.hex(24)
user.password = password
user.password_confirmation = password
user.force_password_change = false
user.save!
token = Token::API.create!(user: user, token_name: 'ai-team infrastructure spike')
puts 'BOOTSTRAP_JSON=' + {OPENPROJECT_ADMIN_PASSWORD: password, OPENPROJECT_API_KEY: token.plain_value}.to_json
''')
    save(data)
    print('OpenProject: admin password changed, bootstrap API key saved in .env.')
else:
    print('OpenProject: existing bootstrap credentials retained.')

if 'BOOKSTACK_TOKEN_ID' not in existing:
    data = run('bookstack', ['php'], r'''<?php
chdir('/app/www');
require 'vendor/autoload.php';
$app = require 'bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
$user = BookStack\Users\Models\User::where('email', 'admin@admin.com')->firstOrFail();
$password = bin2hex(random_bytes(24));
$user->password = Illuminate\Support\Facades\Hash::make($password);
$user->save();
$secret = bin2hex(random_bytes(32));
$token = new BookStack\Api\ApiToken();
$token->user_id = $user->id;
$token->name = 'ai-team infrastructure spike';
$token->token_id = bin2hex(random_bytes(16));
$token->secret = Illuminate\Support\Facades\Hash::make($secret);
$token->expires_at = now()->addDays(30);
$token->save();
echo 'BOOTSTRAP_JSON=' . json_encode(['BOOKSTACK_ADMIN_PASSWORD'=>$password, 'BOOKSTACK_TOKEN_ID'=>$token->token_id, 'BOOKSTACK_TOKEN_SECRET'=>$secret]) . PHP_EOL;
''')
    save(data)
    print('BookStack: admin password changed, 30-day bootstrap API token saved in .env.')
else:
    print('BookStack: existing bootstrap credentials retained.')

# Project types are not writable via the v3 project PATCH in this version.
# Administrative local bootstrap only; runtime agents use API adapters.
run('openproject', ['bundle', 'exec', 'rails', 'runner', '-'], '''
project = Project.find_by(identifier: 'ai-team')
if project
  Type.find_each { |type| ProjectType.find_or_create_by!(project: project, type: type) }
  epic = project.work_packages.find_by(subject: 'Этап 0 — инфраструктура и интеграции')
  if epic
    epic.type = Type.find_by!(name: 'Epic')
    epic.save!
  end
end
puts 'BOOTSTRAP_JSON=' + {configured: true}.to_json
''')
print('OpenProject: available project types configured.')
