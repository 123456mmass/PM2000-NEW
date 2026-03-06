'use client';

import React, { useEffect, useState, useRef, useCallback } from 'react';
import { fetchAlerts } from '@/utils/apiClient';

interface AlertItem {
    id: string; // Unique ID for each spawned alert
    category: string;
    severity: string;
    message: string;
    timestamp: string;
}

const ALERT_POLL_INTERVAL_MS = 1000;
const ALERT_REPEAT_INTERVAL_MS = 2000;

// Helper to play a short beep sound
const playBeep = () => {
    try {
        const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
        const oscillator = audioCtx.createOscillator();
        const gainNode = audioCtx.createGain();

        oscillator.connect(gainNode);
        gainNode.connect(audioCtx.destination);

        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(880, audioCtx.currentTime); // A5 note
        gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime); // Low volume
        gainNode.gain.exponentialRampToValueAtTime(0.00001, audioCtx.currentTime + 0.5);

        oscillator.start(audioCtx.currentTime);
        oscillator.stop(audioCtx.currentTime + 0.5);
    } catch (e) {
        console.error("Audio playback prevented:", e);
    }
};

export function AlertToaster() {
    const [alerts, setAlerts] = useState<AlertItem[]>([]);
    const lastNotifiedAtRef = useRef<Map<string, number>>(new Map());

    const dismissAlert = useCallback((idToRemove: string) => {
        setAlerts(prev => prev.filter(a => a.id !== idToRemove));
    }, []);

    useEffect(() => {
        let isMounted = true;

        const checkAlerts = async () => {
            try {
                const response: any = await fetchAlerts();
                if (!isMounted) return;

                if (response?.status === 'ALERT' && Array.isArray(response?.alerts)) {
                    let hasNewAlert = false;
                    const newAlerts: AlertItem[] = [];
                    const nowMs = Date.now();
                    const now = new Date(nowMs).toLocaleTimeString('th-TH', { hour12: false });
                    const isRetainedAlert = response?.retained === true;

                    response.alerts.forEach((incoming: any) => {
                        const categoryKey = String(incoming.category || 'unknown');
                        const lastNotifiedAt = lastNotifiedAtRef.current.get(categoryKey) ?? 0;
                        const shouldNotify = isRetainedAlert
                            ? lastNotifiedAt === 0
                            : (nowMs - lastNotifiedAt) >= ALERT_REPEAT_INTERVAL_MS;

                        if (!shouldNotify) return;

                        hasNewAlert = true;
                        lastNotifiedAtRef.current.set(categoryKey, nowMs);
                        newAlerts.push({
                            id: `${categoryKey}-${nowMs}`,
                            category: incoming.category,
                            severity: incoming.severity,
                            message: incoming.message,
                            timestamp: now
                        });
                    });

                    if (hasNewAlert) {
                        playBeep();
                        setAlerts(prev => [...prev, ...newAlerts]);
                    }
                } else if (response?.status === 'OK') {
                    // No active alerts according to server.
                    // If faults go away, we DO NOT automatically clear the UI.
                    // The user requested that alerts do not disappear until they click close.
                    // So we do not clear the `alerts` array. 

                    // Clear the cooldown map so a new fault burst notifies immediately.
                    lastNotifiedAtRef.current.clear();
                }

            } catch (error) {
                console.warn('Alert polling failed:', error);
            }
        };

        const interval = setInterval(checkAlerts, ALERT_POLL_INTERVAL_MS);
        checkAlerts();

        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, []);

    if (alerts.length === 0) return null;

    return (
        <div className="fixed bottom-6 right-6 z-[9999] flex flex-col gap-3 max-w-sm pointer-events-none">
            {alerts.slice(-5).map((alert) => ( // Show at most 5 at a time
                <div
                    key={alert.id}
                    className={`
            relative p-4 rounded-lg shadow-xl border-l-4 pointer-events-auto transform transition-all duration-300 animate-slide-in-right
            ${alert.severity === 'critical' || alert.severity === 'high'
                            ? 'bg-red-50/95 dark:bg-red-900/90 border-red-500 text-red-900 dark:text-red-100 backdrop-blur-sm'
                            : alert.severity === 'medium'
                                ? 'bg-yellow-50/95 dark:bg-yellow-900/90 border-yellow-500 text-yellow-900 dark:text-yellow-100 backdrop-blur-sm'
                                : 'bg-blue-50/95 dark:bg-blue-900/90 border-blue-500 text-blue-900 dark:text-blue-100 backdrop-blur-sm'
                        }
          `}
                >
                    {/* Close Button */}
                    <button
                        onClick={() => dismissAlert(alert.id)}
                        className="absolute top-2 right-2 p-1 rounded-md opacity-70 hover:opacity-100 transition-opacity focus:outline-none"
                        aria-label="Dismiss alert"
                    >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"></path>
                        </svg>
                    </button>

                    <div className="flex items-start pr-6">
                        <div className="flex-shrink-0 mt-0.5 mr-3">
                            <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                            </svg>
                        </div>
                        <div className="flex-1 w-full">
                            <div className="flex justify-between items-center mb-1">
                                <p className="text-sm font-bold uppercase tracking-wider opacity-90">
                                    {alert.category.replace('_', ' ')} Warning
                                </p>
                                <span className="text-xs font-mono opacity-70 whitespace-nowrap ml-2">
                                    {alert.timestamp}
                                </span>
                            </div>
                            <p className="text-sm font-medium leading-tight">
                                {alert.message}
                            </p>
                        </div>
                    </div>
                </div>
            ))}
            <style jsx>{`
        @keyframes slide-in-right {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
        .animate-slide-in-right {
          animation: slide-in-right 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
      `}</style>
        </div>
    );
}
