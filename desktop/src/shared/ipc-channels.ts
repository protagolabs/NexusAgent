/**
 * @file ipc-channels.ts
 * @description IPC 通道名常量 — main 和 preload 共用
 *
 * 独立于 electron.app，preload 可安全导入。
 */

export const IPC = {
  // 依赖检测
  CHECK_DEPENDENCIES: 'check-dependencies',
  INSTALL_DEPENDENCY: 'install-dependency',

  // 环境变量
  GET_ENV: 'get-env',
  SET_ENV: 'set-env',
  VALIDATE_ENV: 'validate-env',

  // EverMemOS 环境变量
  GET_EVERMEMOS_ENV: 'get-evermemos-env',
  SET_EVERMEMOS_ENV: 'set-evermemos-env',
  VALIDATE_EVERMEMOS_ENV: 'validate-evermemos-env',

  // Docker
  DOCKER_STATUS: 'docker-status',
  DOCKER_START: 'docker-start',
  DOCKER_STOP: 'docker-stop',

  // 服务进程
  SERVICE_START_ALL: 'service-start-all',
  SERVICE_STOP_ALL: 'service-stop-all',
  SERVICE_RESTART: 'service-restart',
  SERVICE_STATUS: 'service-status',

  // 健康检查
  HEALTH_STATUS: 'health-status',
  HEALTH_SUBSCRIBE: 'health-subscribe',

  // 数据库
  INIT_DATABASE: 'init-database',

  // 一键自动安装
  AUTO_SETUP: 'auto-setup',
  QUICK_START: 'quick-start',
  ON_SETUP_PROGRESS: 'on-setup-progress',

  // Claude Code 认证
  CLAUDE_AUTH_INFO: 'claude-auth-info',
  CLAUDE_LOGIN_START: 'claude-login-start',
  CLAUDE_LOGIN_CANCEL: 'claude-login-cancel',
  CLAUDE_LOGIN_INPUT: 'claude-login-input',
  CLAUDE_SAVE_SETUP_TOKEN: 'claude-save-setup-token',
  ON_CLAUDE_LOGIN_STATUS: 'on-claude-login-status',

  // 杂项
  OPEN_EXTERNAL: 'open-external',
  GET_SETUP_STATE: 'get-setup-state',
  SET_SETUP_COMPLETE: 'set-setup-complete',
  GET_LOGS: 'get-logs',
  ON_LOG: 'on-log',
  ON_HEALTH_UPDATE: 'on-health-update'
} as const
