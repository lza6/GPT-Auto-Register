module.exports = {
  apps: [
    {
      name: 'boterdrop-solver',
      script: './boterdrop_wrapper.py',
      cwd: '/opt/boterdrop-solver',
      interpreter: '/opt/boterdrop-solver/.venv/bin/python',
      exec_mode: 'fork',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      kill_timeout: 15000,
      env: {
        PYTHONUNBUFFERED: '1',
      },
    },
  ],
};
