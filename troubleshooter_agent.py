import tkinter as tk
from tkinter import scrolledtext, messagebox
from google import genai
from google.genai import types
import json
import datetime
import psutil
import subprocess
import threading
import time
from dotenv import load_dotenv

load_dotenv()

client = genai.Client()


# Thread-safe helpers
def ask_yes_no_threadsafe(title, message):
    """Safely prompts a messagebox from a background thread."""
    result = [None]
    event = threading.Event()

    def _ask():
        result[0] = messagebox.askyesno(title, message)
        event.set()

    root.after(0, _ask)
    event.wait()
    return result[0]


# MCP tools (later te vervangen door echte FastMCP server)
def collect_realtime_logs() -> str:
    """Collect realtime system logs (simulated + psutil info for now)."""
    try:
        disk_usage = psutil.disk_usage('/')
        mem = psutil.virtual_memory()
        return f"""
[LOG COLLECTED {datetime.datetime.now()}]
Disk usage: {disk_usage.percent}% full ({disk_usage.free // (1024 ** 3)} GB free)
Memory: {mem.percent}% used
Recent events (simulation):
- Info: System online
- Warning: Low disk space detected on C: (this could be a real incident)
"""
    except Exception as e:
        return f"Log collection error: {str(e)}"


def run_disk_cleaner() -> str:
    """Run Windows Disk Cleaner."""
    try:
        result = subprocess.run(['cleanmgr', '/sagerun:1'],
                                capture_output=True, text=True, timeout=30, shell=True)
        return f"Disk Cleaner executed.\nOutput: {result.stdout or 'Success (no output)'}"
    except Exception as e:
        return f"Disk Cleaner could not start: {str(e)}"


chat_history = []


def process_ai_response(user_input):
    """Background thread: calls Gemini and updates GUI safely"""
    try:
        chat_history.append({"role": "user", "parts": [{"text": user_input}]})

        # First generation
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite-preview',
            contents=chat_history,
            config=types.GenerateContentConfig(
                tools=[collect_realtime_logs, run_disk_cleaner]
            )
        )

        func_name = None
        tool_result = None

        if (response.candidates and
                response.candidates[0].content and
                response.candidates[0].content.parts):

            part = response.candidates[0].content.parts[0]
            func_call = getattr(part, 'function_call', None)

            if func_call is not None and hasattr(func_call, 'name'):
                func_name = func_call.name

                def show_tool_call():
                    chat_window.insert(tk.END, f"Agent calling tool: {func_name}...\n")
                    chat_window.see(tk.END)
                    chat_window.update_idletasks()
                    chat_window.update()

                root.after(0, show_tool_call)

                if func_name == "collect_realtime_logs":
                    tool_result = collect_realtime_logs()
                elif func_name == "run_disk_cleaner":
                    if ask_yes_no_threadsafe("Confirmation", "Do you want to run Disk Cleaner now?"):
                        tool_result = run_disk_cleaner()
                    else:
                        tool_result = "Action cancelled by user."
                else:
                    tool_result = "Unknown tool."

                chat_history.append({
                    "role": "model",
                    "parts": [{"function_call": func_call}]
                })
                chat_history.append({
                    "role": "function",
                    "parts": [{
                        "function_response": {
                            "name": func_name,
                            "response": {"result": tool_result}
                        }
                    }]
                })

                # Second generation after tool execution
                response = client.models.generate_content(
                    model='gemini-3.1-flash-lite-preview',
                    contents=chat_history,
                    config=types.GenerateContentConfig()
                )

        ai_text = ""
        if hasattr(response, 'text') and response.text:
            ai_text = response.text
        elif response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            ai_text = str(response.candidates[0].content.parts[0])

        def update_gui():
            chat_window.insert(tk.END, f"Agent: {ai_text}\n\n")
            chat_window.see(tk.END)
            chat_window.update_idletasks()
            chat_window.update()
            chat_history.append({"role": "model", "parts": [{"text": ai_text}]})

            # Check resolution status
            if any(word in ai_text.lower() for word in ["solved", "fixed", "resolved", "done", "klaar"]):
                if messagebox.askyesno("Issue Status", "Is the issue solved?"):
                    chat_window.insert(tk.END, "Issue marked as resolved.\n")
                    chat_window.see(tk.END)
                    chat_window.update_idletasks()
                    chat_window.update()
                else:
                    export_ticket_json()

            user_entry.config(state=tk.NORMAL)
            send_btn.config(state=tk.NORMAL)

        root.after(0, update_gui)

    except Exception as e:
        error_msg = f"Error: {str(e)}"

        def show_error():
            chat_window.insert(tk.END, f"{error_msg}\n")
            chat_window.see(tk.END)
            chat_window.update_idletasks()
            chat_window.update()
            user_entry.config(state=tk.NORMAL)
            send_btn.config(state=tk.NORMAL)

        root.after(0, show_error)
        print(f"DEBUG - Error in AI thread: {e}")


def send_message():
    user_input = user_entry.get("1.0", tk.END).strip()
    if not user_input:
        return

    chat_window.config(state=tk.NORMAL)
    chat_window.insert(tk.END, f"You: {user_input}\n\n")
    chat_window.see(tk.END)
    chat_window.update_idletasks()
    chat_window.update()
    user_entry.delete("1.0", tk.END)

    user_entry.config(state=tk.DISABLED)
    send_btn.config(state=tk.DISABLED)

    chat_window.insert(tk.END, "Thinking...\n\n")
    chat_window.see(tk.END)
    chat_window.update_idletasks()
    chat_window.update()

    thread = threading.Thread(target=process_ai_response, args=(user_input,), daemon=True)
    thread.start()


def export_ticket_json():
    """Export JSON ticket for future Autotask API integration"""
    ticket = {
        "title": "Automatic Troubleshooting - Disk Issue (via Local Agent)",
        "description": "Agent collected logs and suggested Disk Cleaner. User indicated the issue is not yet resolved.",
        "priority": "Medium",
        "logs": collect_realtime_logs(),
        "timestamp": datetime.datetime.now().isoformat(),
        "source": "Local MCP Troubleshooter Agent v0.5"
    }
    filename = f"autotask_ticket_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(ticket, f, indent=2, ensure_ascii=False)
    messagebox.showinfo("JSON Exported", f"Ticket saved as:\n{filename}\n\nReady for Autotask API integration!")
    chat_window.insert(tk.END, f"JSON exported -> {filename}\n")
    chat_window.see(tk.END)
    chat_window.update_idletasks()
    chat_window.update()


# GUI initialization
root = tk.Tk()
root.title("Local Troubleshooter Agent (MCP PoC)")
root.geometry("900x700")

chat_window = scrolledtext.ScrolledText(root, wrap=tk.WORD, state=tk.NORMAL, font=("Consolas", 10))
chat_window.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

user_entry = tk.Text(root, height=3, bg="white", fg="black", insertbackground="black", font=("Consolas", 10))
user_entry.pack(padx=10, pady=5, fill=tk.X)

send_btn = tk.Button(root, text="Send Message ->", command=send_message, bg="#007ACC", fg="white",
                     font=("Arial", 10, "bold"))
send_btn.pack(pady=5)

user_entry.focus_set()

chat_window.insert(tk.END, "Welcome to the Local Troubleshooter Agent!\n"
                           "Tell me what's wrong (e.g. 'my C: drive is full').\n\n")
chat_window.config(state=tk.DISABLED)


# Placeholder for future realtime log monitoring
def realtime_monitor():
    while True:
        time.sleep(1)


threading.Thread(target=realtime_monitor, daemon=True).start()

root.mainloop()
