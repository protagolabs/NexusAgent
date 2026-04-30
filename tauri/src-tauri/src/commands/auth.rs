//! Claude Code CLI authentication commands.
//!
//! Exposes `trigger_claude_login` to the frontend so the desktop app can
//! initiate the OAuth flow without requiring the user to open a terminal.
//! The command spawns `claude auth login` as a subprocess with the bundled
//! Node.js + CLI paths prepended to PATH (same injection that
//! `process_manager.rs` does for Python services).

use tauri::State;

use crate::state::{resolve_bundled_node_bins, AppState};

/// Spawn `claude auth login` with the correct PATH so the bundled CLI is
/// found even when the .app is launched from Finder (minimal launchd PATH).
///
/// The command opens the user's default browser for Anthropic OAuth. It
/// blocks until the user completes (or cancels) the flow, then returns.
/// Credentials are written to `~/.claude/.credentials.json` — shared by
/// both the bundled and any user-installed `claude` binary.
#[tauri::command]
pub async fn trigger_claude_login(_state: State<'_, AppState>) -> Result<String, String> {
    let parent_path = std::env::var("PATH").unwrap_or_default();
    let bundled_bins = resolve_bundled_node_bins();
    let child_path = if bundled_bins.is_empty() {
        parent_path
    } else {
        let mut parts: Vec<String> = bundled_bins
            .iter()
            .map(|p| p.to_string_lossy().to_string())
            .collect();
        if !parent_path.is_empty() {
            parts.push(parent_path);
        }
        parts.join(":")
    };

    let output = tokio::process::Command::new("claude")
        .args(["auth", "login"])
        .env("PATH", &child_path)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .status()
        .await
        .map_err(|e| format!("Failed to spawn claude auth login: {}", e))?;

    if output.success() {
        Ok("Login completed successfully".to_string())
    } else {
        Err(format!(
            "claude auth login exited with code {}",
            output.code().unwrap_or(-1)
        ))
    }
}

/// Non-blocking status check — mirrors what the Python backend does at
/// `GET /api/providers/claude-status` but runs from the Tauri side so the
/// frontend can poll without waiting for the backend to be fully up.
#[tauri::command]
pub async fn get_claude_login_status(
    _state: State<'_, AppState>,
) -> Result<ClaudeLoginStatus, String> {
    let parent_path = std::env::var("PATH").unwrap_or_default();
    let bundled_bins = resolve_bundled_node_bins();
    let child_path = if bundled_bins.is_empty() {
        parent_path
    } else {
        let mut parts: Vec<String> = bundled_bins
            .iter()
            .map(|p| p.to_string_lossy().to_string())
            .collect();
        if !parent_path.is_empty() {
            parts.push(parent_path);
        }
        parts.join(":")
    };

    let mut status = ClaudeLoginStatus {
        cli_installed: false,
        logged_in: false,
    };

    // Check if claude binary exists on the (augmented) PATH.
    let which_out = tokio::process::Command::new("which")
        .arg("claude")
        .env("PATH", &child_path)
        .output()
        .await;
    if let Ok(w) = &which_out {
        if w.status.success() {
            status.cli_installed = true;
        }
    }

    if !status.cli_installed {
        return Ok(status);
    }

    // Run `claude auth status` and parse JSON output.
    if let Ok(auth_out) = tokio::process::Command::new("claude")
        .args(["auth", "status"])
        .env("PATH", &child_path)
        .output()
        .await
    {
        if auth_out.status.success() {
            let stdout = String::from_utf8_lossy(&auth_out.stdout);
            if stdout.contains("\"loggedIn\":true") || stdout.contains("\"loggedIn\": true") {
                status.logged_in = true;
            }
        }
    }

    Ok(status)
}

#[derive(serde::Serialize, Clone)]
pub struct ClaudeLoginStatus {
    pub cli_installed: bool,
    pub logged_in: bool,
}
