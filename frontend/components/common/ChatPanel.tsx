import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import apiClient from '@/utils/apiClient';

interface Message {
    role: 'user' | 'assistant';
    content: string;
}

export const ChatPanel = () => {
    const [isOpen, setIsOpen] = useState(false);
    const [messages, setMessages] = useState<Message[]>([
        { role: 'assistant', content: 'สวัสดีครับ ผมคือ AI Advisor ประจำระบบ PM2000 มีอะไรให้ผมช่วยตรวจสอบหรือวิเคราะห์ข้อมูลจากมิเตอร์ไหมครับ?' }
    ]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);

    // Load from sessionStorage on mount
    useEffect(() => {
        const savedMessages = sessionStorage.getItem('pm2000_chat_messages');
        const savedIsOpen = sessionStorage.getItem('pm2000_chat_open');

        if (savedMessages) {
            try {
                setMessages(JSON.parse(savedMessages));
            } catch (e) {
                console.error('Failed to parse saved messages', e);
            }
        }

        if (savedIsOpen === 'true') {
            setIsOpen(true);
        }

        // Clear saved position to reset to default
        sessionStorage.removeItem('pm2000_chat_pos');
    }, []);

    // Save to sessionStorage when state changes
    useEffect(() => {
        if (messages.length > 1) {
            sessionStorage.setItem('pm2000_chat_messages', JSON.stringify(messages));
        }
    }, [messages]);

    useEffect(() => {
        sessionStorage.setItem('pm2000_chat_open', String(isOpen));
    }, [isOpen]);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages]);

    // Listen for external events to open chat with context
    useEffect(() => {
        const handleOpenChat = (event: CustomEvent) => {
            const { context, source } = event.detail || {};
            
            // Open chat
            setIsOpen(true);
            
            // Add context message from user
            if (context) {
                const contextMsg: Message = { 
                    role: 'user', 
                    content: `[จากผลวิเคราะห์${source || 'AI'}]\n${context}\n\nขอสอบถามเพิ่มเติมเกี่ยวกับผลวิเคราะห์นี้ครับ`
                };
                setMessages(prev => [...prev, contextMsg]);
            }
        };

        // @ts-ignore - CustomEvent listener
        window.addEventListener('open-chat-with-context', handleOpenChat);
        
        // @ts-ignore
        return () => window.removeEventListener('open-chat-with-context', handleOpenChat);
    }, []);

    const handleSend = async () => {
        if (!input.trim() || isLoading) return;

        const userMsg: Message = { role: 'user', content: input };
        const newMessages = [...messages, userMsg];
        setMessages(newMessages);
        setInput('');
        setIsLoading(true);

        try {
            const response = await apiClient.postChat(newMessages);
            setMessages(prev => [...prev, { role: 'assistant', content: response.response }]);
        } catch (err) {
            console.error('Chat error:', err);
            setMessages(prev => [...prev, { role: 'assistant', content: 'ขออภัยครับ เกิดข้อผิดพลาดในการเชื่อมต่อระบบวิเคราะห์ AI' }]);
        } finally {
            setIsLoading(false);
        }
    };

    const handleClearChat = () => {
        if (confirm('คุณต้องการล้างประวัติการแชททั้งหมดใช่หรือไม่?')) {
            const initialMessage: Message[] = [
                { role: 'assistant', content: 'สวัสดีครับ ผมคือ AI Advisor ประจำระบบ PM2000 มีอะไรให้ผมช่วยตรวจสอบหรือวิเคราะห์ข้อมูลจากมิเตอร์ไหมครับ?' }
            ];
            setMessages(initialMessage);
            sessionStorage.removeItem('pm2000_chat_messages');
        }
    };

    return (
        <div className="fixed bottom-6 left-6 z-50 flex flex-col items-start transition-all duration-300">
            {/* Chat Window */}
            {isOpen && (
                <div className="mb-4 flex h-[500px] w-[380px] flex-col overflow-hidden rounded-2xl border border-white/20 bg-slate-900/90 shadow-2xl backdrop-blur-xl transition-all animate-in fade-in slide-in-from-bottom-4 duration-300">
                    {/* Header */}
                    <div className="flex items-center justify-between border-b border-white/10 bg-indigo-600/20 px-4 py-3">
                        <div className="flex items-center gap-2">
                            <div className="h-2 w-2 animate-pulse rounded-full bg-green-400" />
                            <span className="font-semibold text-white">AI Advisor 🤖</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={handleClearChat}
                                title="ล้างประวัติการแชท"
                                className="text-white/40 hover:text-rose-400 p-1 transition-colors"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                            </button>
                            <button
                                onClick={() => setIsOpen(false)}
                                className="text-white/60 hover:text-white p-1"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                                </svg>
                            </button>
                        </div>
                    </div>

                    {/* Messages Container */}
                    <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
                        {messages.map((m, i) => (
                            <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                                <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${m.role === 'user'
                                    ? 'bg-indigo-600 text-white rounded-tr-none'
                                    : 'bg-white/10 text-slate-100 rounded-tl-none border border-white/5'
                                    }`}>
                                    <div className="markdown-content prose prose-invert prose-sm max-w-none text-slate-100">
                                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                            {m.content}
                                        </ReactMarkdown>
                                    </div>
                                </div>
                            </div>
                        ))}
                        {isLoading && (
                            <div className="flex justify-start">
                                <div className="bg-white/10 rounded-2xl px-4 py-3 rounded-tl-none border border-white/5 flex gap-1 items-center">
                                    <div className="w-1 h-1 bg-white/40 rounded-full animate-bounce [animation-delay:-0.3s]" />
                                    <div className="w-1 h-1 bg-white/40 rounded-full animate-bounce [animation-delay:-0.15s]" />
                                    <div className="w-1 h-1 bg-white/40 rounded-full animate-bounce" />
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Input Area */}
                    <div className="p-4 border-t border-white/10 bg-slate-800/50">
                        <div className="relative flex items-center">
                            <input
                                type="text"
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                                placeholder="ถามเรื่องไฟฟ้า..."
                                className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/50 placeholder:text-white/30"
                            />
                            <button
                                onClick={handleSend}
                                disabled={isLoading || !input.trim()}
                                className="absolute right-2 text-indigo-400 hover:text-indigo-300 disabled:text-white/20"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 rotate-90" viewBox="0 0 20 20" fill="currentColor">
                                    <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Toggle Button */}
            <button
                onClick={() => setIsOpen(!isOpen)}
                className={`flex h-14 w-14 items-center justify-center rounded-fill shadow-xl transition-all duration-300 hover:scale-110 active:scale-95 ${isOpen ? 'bg-slate-700' : 'bg-indigo-600 hover:bg-indigo-500'
                    }`}
                style={{ borderRadius: '50%' }}
            >
                {isOpen ? (
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                ) : (
                    <div className="relative">
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-7 w-7 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                        </svg>
                        {!isOpen && messages.length > 0 && (
                            <div className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white">
                                1
                            </div>
                        )}
                    </div>
                )}
            </button>
        </div>
    );
};
