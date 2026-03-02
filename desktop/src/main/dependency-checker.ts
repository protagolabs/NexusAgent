/**
 * @file dependency-checker.ts
 * @description Detect installed system dependencies and provide installation guidance
 *
 * Migrated from run.sh do_install logic, reimplemented in TypeScript.
 * Checks: uv, Python (>=3.13), Node.js (>=20), Docker, Claude CLI
 */

import { execFile } from 'child_process'
import { promisify } from 'util'
import { shell } from 'electron'
import { getShellEnv } from './shell-env'

const execFileAsync = promisify(execFile)

// ─── Type Definitions ───────────────────────────────────────

export interface DependencyStatus {
  id: string
  name: string
  required: boolean
  installed: boolean
  version: string | null
  /** Minimum version requirement (semver) */
  minVersion: string | null
  /** Installation instructions */
  installHint: string
  /** Auto-install command (null means manual install required) */
  autoInstallCommand: string[] | null
  /** Download URL (for manual installation) */
  downloadUrl: string | null
}

// ─── Version Comparison ───────────────────────────────────────

/** Parse major version number for simple >= comparison */
function parseMajorVersion(versionStr: string): number {
  const match = versionStr.match(/(\d+)/)
  return match ? parseInt(match[1], 10) : 0
}

/** Parse major.minor version */
function parseVersion(versionStr: string): [number, number] {
  const match = versionStr.match(/(\d+)\.(\d+)/)
  if (!match) return [0, 0]
  return [parseInt(match[1], 10), parseInt(match[2], 10)]
}

/** Version a >= version b */
function versionGte(a: string, b: string): boolean {
  const [aMajor, aMinor] = parseVersion(a)
  const [bMajor, bMinor] = parseVersion(b)
  if (aMajor !== bMajor) return aMajor > bMajor
  return aMinor >= bMinor
}

// ─── Individual Check Functions ───────────────────────────────────

async function execSafe(cmd: string, args: string[]): Promise<string> {
  try {
    const { stdout } = await execFileAsync(cmd, args, {
      timeout: 10000,
      env: getShellEnv()
    })
    return stdout.trim()
  } catch {
    return ''
  }
}

async function checkUv(): Promise<DependencyStatus> {
  const output = await execSafe('uv', ['--version'])
  const version = output.match(/uv (\S+)/)?.[1] ?? null
  return {
    id: 'uv',
    name: 'uv (Python package manager)',
    required: true,
    installed: !!version,
    version,
    minVersion: null,
    installHint: 'Run: curl -LsSf https://astral.sh/uv/install.sh | sh',
    autoInstallCommand: ['sh', '-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'],
    downloadUrl: 'https://docs.astral.sh/uv/getting-started/installation/'
  }
}

async function checkPython(): Promise<DependencyStatus> {
  const output = await execSafe('uv', ['run', 'python', '--version'])
  const version = output.match(/Python (\S+)/)?.[1] ?? null
  const meetsMin = version ? versionGte(version, '3.13') : false
  return {
    id: 'python',
    name: 'Python (>=3.13)',
    required: true,
    installed: !!version && meetsMin,
    version,
    minVersion: '3.13',
    installHint: 'uv manages Python versions automatically. Install uv first.',
    autoInstallCommand: null,
    downloadUrl: null
  }
}

async function checkNode(): Promise<DependencyStatus> {
  const output = await execSafe('node', ['--version'])
  const version = output.replace(/^v/, '') || null
  const meetsMin = version ? parseMajorVersion(version) >= 20 : false
  return {
    id: 'node',
    name: 'Node.js (>=20)',
    required: true,
    installed: !!version && meetsMin,
    version,
    minVersion: '20',
    installHint: 'Download from https://nodejs.org',
    autoInstallCommand: null,
    downloadUrl: 'https://nodejs.org/en/download/'
  }
}

async function checkDocker(): Promise<DependencyStatus> {
  const output = await execSafe('docker', ['--version'])
  const version = output.match(/Docker version (\S+)/)?.[1]?.replace(/,/, '') ?? null
  // Additionally check if Docker daemon is running
  const infoOutput = await execSafe('docker', ['info', '--format', '{{.ServerVersion}}'])
  const daemonRunning = !!infoOutput
  return {
    id: 'docker',
    name: 'Docker Desktop',
    required: true,
    installed: !!version && daemonRunning,
    version: daemonRunning ? version : version ? `${version} (not running)` : null,
    minVersion: '20.10',
    installHint: daemonRunning
      ? ''
      : version
        ? 'Please start Docker Desktop'
        : 'Download from https://docker.com/products/docker-desktop',
    autoInstallCommand: null,
    downloadUrl: 'https://www.docker.com/products/docker-desktop/'
  }
}

async function checkClaude(): Promise<DependencyStatus> {
  const output = await execSafe('claude', ['--version'])
  const version = output || null
  return {
    id: 'claude',
    name: 'Claude CLI',
    required: true,
    installed: !!version,
    version,
    minVersion: null,
    installHint: 'Run: npm install -g @anthropic-ai/claude-code',
    autoInstallCommand: ['npm', 'install', '-g', '@anthropic-ai/claude-code'],
    downloadUrl: null
  }
}

// ─── Public API ───────────────────────────────────────

/** Check all system dependencies, return status list */
export async function checkAllDependencies(): Promise<DependencyStatus[]> {
  const results = await Promise.all([
    checkUv(),
    checkPython(),
    checkNode(),
    checkDocker(),
    checkClaude()
  ])
  return results
}

/** Attempt to auto-install a dependency (only supported for items with autoInstallCommand) */
export async function installDependency(
  depId: string
): Promise<{ success: boolean; output: string }> {
  const deps = await checkAllDependencies()
  const dep = deps.find((d) => d.id === depId)

  if (!dep) {
    return { success: false, output: `Unknown dependency: ${depId}` }
  }

  if (!dep.autoInstallCommand) {
    // Open download URL
    if (dep.downloadUrl) {
      await shell.openExternal(dep.downloadUrl)
      return { success: false, output: `Download page opened. Please install manually: ${dep.downloadUrl}` }
    }
    return { success: false, output: `Auto-install not supported: ${dep.installHint}` }
  }

  try {
    const [cmd, ...args] = dep.autoInstallCommand
    const { stdout, stderr } = await execFileAsync(cmd, args, {
      timeout: 120000,
      env: getShellEnv()
    })
    return { success: true, output: stdout + stderr }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err)
    return { success: false, output: `Installation failed: ${message}` }
  }
}
