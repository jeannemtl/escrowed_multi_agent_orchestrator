'use client';

import { useRef, useState } from 'react';

interface SearchBarProps {
  connected: boolean;
  plannerStatus: string;
  plannerStatusColor: string;
  onPlan: (prompt: string, image: string | null) => void;
  onPromptImageChange: (prompt: string, image: string | null) => void;
}

export default function SearchBar({
  connected,
  plannerStatus,
  plannerStatusColor,
  onPlan,
  onPromptImageChange,
}: SearchBarProps) {
  const [prompt, setPrompt] = useState('');
  const [imageB64, setImageB64] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<string>('');
  const fileRef = useRef<HTMLInputElement>(null);

  const hasContent = prompt.trim().length > 0 || !!imageB64;
  const planDisabled = !hasContent || !connected;

  function handleImageUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const result = ev.target?.result as string;
      setImagePreview(result);
      const b64 = result.split(',')[1];
      setImageB64(b64);
      onPromptImageChange(prompt, b64);
    };
    reader.readAsDataURL(file);
  }

  function clearImage() {
    setImageB64(null);
    setImagePreview('');
    if (fileRef.current) fileRef.current.value = '';
    onPromptImageChange(prompt, null);
  }

  function handlePromptChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const val = e.target.value;
    setPrompt(val);
    onPromptImageChange(val, imageB64);
  }

  function handlePlan() {
    onPlan(prompt, imageB64);
  }

  return (
    <section className="search-section">
      <div className="search-bar">
        <textarea
          className="prompt-input"
          placeholder="Describe what you want the agents to do…"
          value={prompt}
          onChange={handlePromptChange}
          rows={1}
        />
        <label className="image-btn" htmlFor="image-upload">
          📷 Image
        </label>
        <input
          id="image-upload"
          type="file"
          accept="image/*"
          ref={fileRef}
          style={{ display: 'none' }}
          onChange={handleImageUpload}
        />
        {imageB64 && (
          <div className="image-preview-wrap">
            <img className="image-preview" src={imagePreview} alt="preview" />
            <button className="image-clear" onClick={clearImage}>
              remove
            </button>
          </div>
        )}
        <button className="plan-btn" onClick={handlePlan} disabled={planDisabled}>
          Plan &amp; Submit ➜
        </button>
      </div>
      <div className="planner-status">
        {plannerStatus && (
          <span style={{ color: plannerStatusColor }}>{plannerStatus}</span>
        )}
      </div>
    </section>
  );
}
