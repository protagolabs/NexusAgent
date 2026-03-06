/**
 * @file_name: SkillsPanel.tsx
 * @author: Bin Liang
 * @date: 2026-02-03
 * @description: Skills management panel
 *
 * Features:
 * - Display user's installed Skills
 * - Support installing Skills from GitHub or zip files
 * - Support disabling/enabling/removing Skills
 * - Support Study feature: Agent automatically learns Skill documentation
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Puzzle,
  RefreshCw,
  Github,
  FileArchive,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import type { SkillInfo } from '@/types/skills';
import { SkillCard } from './SkillCard';
import { InstallDialog } from './InstallDialog';
import type { InstallMode } from './InstallDialog';

export function SkillsPanel() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [installMode, setInstallMode] = useState<InstallMode>(null);
  const [isInstalling, setIsInstalling] = useState(false);
  const [togglingSkill, setTogglingSkill] = useState<string | null>(null);
  const [removingSkill, setRemovingSkill] = useState<string | null>(null);
  const [studyingSkill, setStudyingSkill] = useState<string | null>(null);
  const [showDisabled, setShowDisabled] = useState(false);

  // Interval ref for polling study status
  const studyPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { agentId, userId } = useConfigStore();

  // Load Skills
  const loadSkills = useCallback(async () => {
    if (!agentId || !userId) return;

    setLoading(true);
    setError(null);

    try {
      const response = await api.listSkills(agentId, userId, showDisabled);
      setSkills(response.skills);
    } catch (err) {
      console.error('Failed to load skills:', err);
      setError(err instanceof Error ? err.message : 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  }, [agentId, userId, showDisabled]);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  // After page load, check if any Skill is being studied and auto-resume polling
  useEffect(() => {
    const studyingSkills = skills.filter((s) => s.study_status === 'studying');
    if (studyingSkills.length > 0 && !studyPollRef.current) {
      setStudyingSkill(studyingSkills[0].name);
      startStudyPolling(studyingSkills[0].name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skills]);

  // Clean up polling on component unmount
  useEffect(() => {
    return () => {
      if (studyPollRef.current) {
        clearInterval(studyPollRef.current);
      }
    };
  }, []);

  // Start study status polling
  const startStudyPolling = (skillName: string) => {
    // Clear existing polling
    if (studyPollRef.current) {
      clearInterval(studyPollRef.current);
    }

    studyPollRef.current = setInterval(async () => {
      if (!agentId || !userId) return;

      try {
        const status = await api.getSkillStudyStatus(skillName, agentId, userId);
        if (status.study_status === 'completed' || status.study_status === 'failed') {
          // Study completed or failed, stop polling, refresh list
          if (studyPollRef.current) {
            clearInterval(studyPollRef.current);
            studyPollRef.current = null;
          }
          setStudyingSkill(null);
          loadSkills();
        }
      } catch (err) {
        console.error('Failed to poll study status:', err);
      }
    }, 3000);
  };

  // Install Skill
  const handleInstall = async (data: {
    url?: string;
    branch?: string;
    file?: File;
  }) => {
    if (!agentId || !userId) return;

    setIsInstalling(true);

    try {
      if (installMode === 'github' && data.url) {
        await api.installSkillFromGithub(
          agentId,
          userId,
          data.url,
          data.branch || 'main'
        );
      } else if (installMode === 'zip' && data.file) {
        await api.installSkillFromZip(agentId, userId, data.file);
      }

      setInstallMode(null);
      loadSkills();
    } catch (err) {
      console.error('Failed to install skill:', err);
      alert(err instanceof Error ? err.message : 'Failed to install skill');
    } finally {
      setIsInstalling(false);
    }
  };

  // Toggle Skill status
  const handleToggle = async (skill: SkillInfo) => {
    if (!agentId || !userId) return;

    setTogglingSkill(skill.name);

    try {
      if (skill.disabled) {
        await api.enableSkill(skill.name, agentId, userId);
      } else {
        await api.disableSkill(skill.name, agentId, userId);
      }
      loadSkills();
    } catch (err) {
      console.error('Failed to toggle skill:', err);
      alert(err instanceof Error ? err.message : 'Failed to toggle skill');
    } finally {
      setTogglingSkill(null);
    }
  };

  // Remove Skill
  const handleRemove = async (skill: SkillInfo) => {
    if (!agentId || !userId) return;

    if (!confirm(`Are you sure you want to remove "${skill.name}"? This action cannot be undone.`)) {
      return;
    }

    setRemovingSkill(skill.name);

    try {
      await api.removeSkill(skill.name, agentId, userId);
      loadSkills();
    } catch (err) {
      console.error('Failed to remove skill:', err);
      alert(err instanceof Error ? err.message : 'Failed to remove skill');
    } finally {
      setRemovingSkill(null);
    }
  };

  // Trigger Skill study
  const handleStudy = async (skill: SkillInfo) => {
    if (!agentId || !userId) return;

    setStudyingSkill(skill.name);

    try {
      const response = await api.studySkill(skill.name, agentId, userId);
      if (response.success) {
        // Start polling
        startStudyPolling(skill.name);
        // Immediately refresh list to show studying status
        loadSkills();
      } else {
        alert(response.message || 'Failed to start study');
        setStudyingSkill(null);
      }
    } catch (err) {
      console.error('Failed to start skill study:', err);
      alert(err instanceof Error ? err.message : 'Failed to start study');
      setStudyingSkill(null);
    }
  };

  return (
    <Card variant="glass" className="flex flex-col h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-[var(--accent-secondary)]/10 flex items-center justify-center">
            <Puzzle className="w-4 h-4 text-[var(--accent-secondary)]" />
          </div>
          <span>Skills</span>
        </CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant={skills.length > 0 ? 'accent' : 'default'} className="font-mono">
            {skills.length}
          </Badge>
          <Button
            variant="ghost"
            size="icon"
            onClick={loadSkills}
            disabled={loading}
            title="Refresh"
            className="hover:bg-[var(--accent-glow)]"
          >
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          </Button>
        </div>
      </CardHeader>

      {/* Action bar */}
      <div className="px-4 pb-3 flex items-center justify-between gap-2">
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setInstallMode('github')}
            className="text-xs"
          >
            <Github className="w-3.5 h-3.5 mr-1.5" />
            From GitHub
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setInstallMode('zip')}
            className="text-xs"
          >
            <FileArchive className="w-3.5 h-3.5 mr-1.5" />
            From Zip
          </Button>
        </div>

        <label className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] cursor-pointer">
          <input
            type="checkbox"
            checked={showDisabled}
            onChange={(e) => setShowDisabled(e.target.checked)}
            className="rounded border-[var(--border-default)]"
          />
          Show disabled
        </label>
      </div>

      <CardContent className="flex-1 overflow-hidden min-h-0">
        {error ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center p-8">
              <div className="w-14 h-14 rounded-2xl bg-[var(--color-error)]/10 mx-auto mb-4 flex items-center justify-center">
                <AlertCircle className="w-7 h-7 text-[var(--color-error)]" />
              </div>
              <p className="text-[var(--color-error)] text-sm font-medium mb-1">Error</p>
              <p className="text-[var(--text-tertiary)] text-xs">{error}</p>
              <Button variant="ghost" size="sm" onClick={loadSkills} className="mt-4">
                <RefreshCw className="w-3 h-3 mr-1.5" />
                Retry
              </Button>
            </div>
          </div>
        ) : loading ? (
          <div className="h-full flex items-center justify-center">
            <Loader2 className="w-8 h-8 text-[var(--accent-primary)] animate-spin" />
          </div>
        ) : skills.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center p-8">
              <div className="w-14 h-14 rounded-2xl bg-[var(--accent-secondary)]/10 mx-auto mb-4 flex items-center justify-center">
                <Puzzle className="w-7 h-7 text-[var(--accent-secondary)]" />
              </div>
              <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">
                No skills installed
              </p>
              <p className="text-[var(--text-tertiary)] text-xs">
                Install skills from GitHub or upload a zip file
              </p>
            </div>
          </div>
        ) : (
          <div className="h-full overflow-y-auto space-y-2 py-2">
            {skills.map((skill) => (
              <SkillCard
                key={skill.name}
                skill={skill}
                onToggle={handleToggle}
                onRemove={handleRemove}
                onStudy={handleStudy}
                isToggling={togglingSkill === skill.name}
                isRemoving={removingSkill === skill.name}
                isStudying={studyingSkill === skill.name}
              />
            ))}
          </div>
        )}
      </CardContent>

      {/* Install dialog */}
      <InstallDialog
        mode={installMode}
        onClose={() => setInstallMode(null)}
        onInstall={handleInstall}
        isInstalling={isInstalling}
      />
    </Card>
  );
}

export default SkillsPanel;
