"""
Public web dashboard — LIMITED VIEW.

Only shows:
  - Clone count
  - Active loops
  - Mirror status
  - Music player (upload & play .m4a files)

All admin operations (make/delete/convert sessions, etc.) are removed.
Those are only accessible via internal admin API + CLI client.
"""

__TCM_FILE_HASH__ = "7425531586"


import time
import re
import logging
import collections
import threading

from flask import (
    Flask,
    jsonify,
    render_template_string,
    request,
    redirect,
    url_for,
    session as flask_session,
    send_file,
    abort,
)
from waitress import serve

from config import WEB_PASSWORD, WEB_SECRET_KEY
from music_manager import (
    list_music_files,
    save_music_file,
    delete_music_file,
    get_music_file_path,
)

app = Flask(__name__)
app.secret_key = WEB_SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 55 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

_automation = None
_logging_ready = False
_logging_lock = threading.Lock()


def set_automation(automation):
    global _automation
    _automation = automation






log_buffer = collections.deque(maxlen=500)
ANSI_STRIP = re.compile(r'\033\[[0-9;]*m')


class WebLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            clean_msg = ANSI_STRIP.sub('', msg)
            log_buffer.append({
                "time": time.strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": clean_msg,
            })
        except Exception:
            pass


def setup_web_logging():
    global _logging_ready

    with _logging_lock:
        if _logging_ready:
            return

        handler = WebLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(handler)
        _logging_ready = True


def is_logged_in():
    return flask_session.get("logged_in", False)






LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#0d1117;display:flex;justify-content:center;align-items:center;min-height:100vh;font-family:'Segoe UI',sans-serif}
        .box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px;width:380px;text-align:center}
        .box h1{color:#58a6ff;font-size:24px;margin-bottom:8px}
        .box p{color:#8b949e;font-size:14px;margin-bottom:30px}
        input[type=password]{width:100%;padding:12px 16px;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:15px;margin-bottom:15px;outline:none}
        input[type=password]:focus{border-color:#58a6ff}
        button{width:100%;padding:12px;background:#238636;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}
        button:hover{background:#2ea043}
        .err{color:#f85149;font-size:13px;margin-bottom:15px}
    </style>
</head>
<body>
<div class="box">
    <h1>⚡ Dashboard</h1>
    <p>Enter password</p>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <form method="POST">
        <input type="password" name="password" placeholder="Password" autofocus>
        <button type="submit">Login</button>
    </form>
</div>
</body>
</html>
"""






DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',Tahoma,sans-serif;min-height:100vh;padding-bottom:200px}
        .hdr{background:linear-gradient(135deg,#161b22,#1c2128);border-bottom:1px solid #30363d;padding:16px 30px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
        .hdr h1{font-size:20px;color:#58a6ff;display:flex;align-items:center;gap:8px}
        .hdr-r{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
        .hdr .tm{font-size:13px;color:#8b949e}
        .btn{padding:6px 14px;border-radius:6px;font-size:13px;cursor:pointer;border:1px solid #30363d;transition:all .2s;text-decoration:none;display:inline-block}
        .btn-g{background:#21262d;color:#8b949e}
        .btn-g:hover{background:#30363d;color:#e6edf3;border-color:#58a6ff}
        .btn-d{background:#21262d;color:#f85149}
        .btn-d:hover{background:#da3633;color:#fff}
        .btn-ok{background:#238636;color:#fff;border-color:#238636}
        .btn-ok:hover{background:#2ea043}
        .btn-b{background:#1f6feb;color:#fff;border-color:#1f6feb}
        .btn-b:hover{background:#388bfd}
        .ct{max-width:1000px;margin:0 auto;padding:25px 30px}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:15px;margin-bottom:20px}
        .card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin-bottom:15px}
        .card-t{font-size:12px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}
        .sn{font-size:36px;font-weight:700;color:#58a6ff;line-height:1}
        .sl{font-size:12px;color:#8b949e;margin-top:6px}
        .li{padding:10px 14px;background:#0d1117;border:1px solid #30363d;border-radius:8px;margin-bottom:8px}
        .li-c{font-size:12px;color:#8b949e}
        .li-t{font-size:14px;color:#58a6ff;font-family:monospace}
        .empty{text-align:center;color:#8b949e;padding:20px;font-size:13px}

        /* Music Player */
        .player{position:fixed;bottom:0;left:0;right:0;background:linear-gradient(0deg,#0d1117,#161b22);border-top:2px solid #30363d;padding:15px 30px;z-index:99;box-shadow:0 -4px 20px rgba(0,0,0,0.5)}
        .player-inner{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:10px}
        .player-top{display:flex;align-items:center;gap:15px;flex-wrap:wrap}
        .player audio{flex:1;min-width:250px;max-width:100%}
        .track-info{font-size:13px;color:#8b949e;min-width:200px;flex:1}
        .track-info strong{color:#e6edf3}
        .player-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
        .music-list{max-height:180px;overflow-y:auto;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:6px}
        .music-item{display:flex;align-items:center;justify-content:space-between;padding:6px 10px;border-radius:6px;cursor:pointer;transition:background .15s}
        .music-item:hover{background:#161b22}
        .music-item.active{background:#1f6feb;color:#fff}
        .music-item-name{flex:1;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:10px}
        .music-item-size{font-size:11px;color:#8b949e;margin-right:8px}
        .music-item .btn-d{padding:2px 8px;font-size:11px}
        .upload-area{padding:10px 14px;background:#0d1117;border:1px dashed #30363d;border-radius:8px;text-align:center;cursor:pointer;transition:all .2s;font-size:13px;color:#8b949e}
        .upload-area:hover{border-color:#58a6ff;color:#e6edf3}
        .upload-area.dragover{border-color:#3fb950;background:rgba(63,185,80,.1);color:#3fb950}
        .badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:500}
        .b-on{background:rgba(63,185,80,.15);color:#3fb950;border:1px solid rgba(63,185,80,.3)}
        .b-off{background:rgba(248,81,73,.15);color:#f85149;border:1px solid rgba(248,81,73,.3)}
        .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px}
        .dg{background:#3fb950}
        .dr{background:#f85149}
        .pulse{animation:pulse 2s infinite}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

        @media(max-width:600px){
            .ct{padding:15px}
            .hdr{padding:12px 15px}
            .player{padding:10px 15px}
        }
    </style>
</head>
<body>

<div class="hdr">
    <h1>⚡ Dashboard</h1>
    <div class="hdr-r">
        <span class="tm" id="clock"></span>
        <button class="btn btn-g" onclick="loadStatus()">↻</button>
        <a class="btn btn-d" href="/logout">Logout</a>
    </div>
</div>

<div class="ct">
    <div class="grid">
        <div class="card" style="text-align:center;margin-bottom:0">
            <div class="card-t">Clones</div>
            <div class="sn" id="s-c">—</div>
            <div class="sl">Total connected</div>
        </div>
        <div class="card" style="text-align:center;margin-bottom:0">
            <div class="card-t">Active Loops</div>
            <div class="sn" id="s-l" style="color:#d29922">—</div>
            <div class="sl">Currently running</div>
        </div>
        <div class="card" style="text-align:center;margin-bottom:0">
            <div class="card-t">Mirror Mode</div>
            <div class="sn" id="s-m" style="font-size:26px;padding-top:5px">—</div>
            <div class="sl">Text + Media</div>
        </div>
    </div>

    <div class="card">
        <div class="card-t"><span class="dot dr pulse"></span> Active Loops</div>
        <div id="ll"><div class="empty">Loading…</div></div>
    </div>
</div>

<!-- Music Player -->
<div class="player">
    <div class="player-inner">
        <div class="player-top">
            <div class="track-info" id="track-info">
                🎵 No track selected
            </div>
            <div class="player-controls">
                <button class="btn btn-g" onclick="togglePlaylist()" id="btn-playlist">☰ Playlist</button>
                <button class="btn btn-g" onclick="prevTrack()">⏮</button>
                <button class="btn btn-g" onclick="nextTrack()">⏭</button>
            </div>
        </div>
        <audio id="audio-player" controls preload="metadata"></audio>

        <div id="playlist-container" style="display:none">
            <div class="upload-area" id="upload-area" onclick="document.getElementById('file-input').click()">
                📁 Click here or drag & drop .m4a files to upload
            </div>
            <input type="file" id="file-input" accept=".m4a" multiple style="display:none" onchange="uploadFiles(this.files)">
            <div class="music-list" id="music-list">
                <div class="empty">Loading tracks…</div>
            </div>
        </div>
    </div>
</div>

<script>
function uc(){document.getElementById('clock').textContent=new Date().toLocaleTimeString('en-US',{hour12:false})}
setInterval(uc,1000);uc();

// ─── Status ─────────────────────────────────────────────────
async function loadStatus(){
    try{
        const r=await fetch('/api/status');
        const d=await r.json();
        if(d.error){location.href='/login';return;}

        document.getElementById('s-c').textContent=d.total_clones;
        document.getElementById('s-l').textContent=d.active_loops;

        const m=document.getElementById('s-m');
        m.textContent=d.mirror_mode?'ON ✓':'OFF';
        m.style.color=d.mirror_mode?'#3fb950':'#f85149';

        const ll=document.getElementById('ll');
        if(!d.loops.length){
            ll.innerHTML='<div class="empty">No active loops</div>';
        }else{
            ll.innerHTML=d.loops.map(l=>`
                <div class="li">
                    <div class="li-c">Chat: ${l.chat_id}</div>
                    <div class="li-t">"${l.text}"</div>
                </div>`).join('');
        }
    }catch(e){console.error(e)}
}

// ─── Music Player ───────────────────────────────────────────
const audio=document.getElementById('audio-player');
const trackInfo=document.getElementById('track-info');
const musicList=document.getElementById('music-list');
const playlistContainer=document.getElementById('playlist-container');
let playlist=[];
let currentTrackIndex=-1;

function togglePlaylist(){
    const isHidden=playlistContainer.style.display==='none';
    playlistContainer.style.display=isHidden?'block':'none';
    document.getElementById('btn-playlist').textContent=isHidden?'✕ Close':'☰ Playlist';
}

async function loadMusic(){
    try{
        const r=await fetch('/api/music/list');
        const d=await r.json();
        playlist=d.files||[];
        renderPlaylist();
    }catch(e){console.error('Load music:',e)}
}

function renderPlaylist(){
    if(playlist.length===0){
        musicList.innerHTML='<div class="empty">No tracks. Upload some .m4a files!</div>';
        return;
    }
    musicList.innerHTML=playlist.map((track,i)=>`
        <div class="music-item ${i===currentTrackIndex?'active':''}" onclick="playTrack(${i})">
            <div class="music-item-name">🎵 ${track.name}</div>
            <div class="music-item-size">${track.size_mb} MB</div>
            <button class="btn btn-d" onclick="event.stopPropagation();deleteTrack('${track.name.replace(/'/g,"\\\\'")}')">✕</button>
        </div>
    `).join('');
}

function playTrack(index){
    if(index<0||index>=playlist.length)return;
    currentTrackIndex=index;
    const track=playlist[index];
    audio.src=`/api/music/stream/${encodeURIComponent(track.name)}`;
    audio.play().catch(e=>console.error('Play error:',e));
    trackInfo.innerHTML=`🎵 <strong>${track.name}</strong>`;
    renderPlaylist();
}

function nextTrack(){
    if(playlist.length===0)return;
    const next=(currentTrackIndex+1)%playlist.length;
    playTrack(next);
}

function prevTrack(){
    if(playlist.length===0)return;
    const prev=(currentTrackIndex-1+playlist.length)%playlist.length;
    playTrack(prev);
}

audio.addEventListener('ended',nextTrack);

async function uploadFiles(files){
    if(!files||files.length===0)return;

    for(const file of files){
        if(!file.name.toLowerCase().endsWith('.m4a')){
            alert(`Skipping ${file.name}: only .m4a files allowed`);
            continue;
        }

        const formData=new FormData();
        formData.append('file',file);

        try{
            trackInfo.innerHTML=`⬆️ Uploading <strong>${file.name}</strong>…`;
            const r=await fetch('/api/music/upload',{method:'POST',body:formData});
            const d=await r.json();
            if(!d.success){
                alert(`Upload failed: ${d.message}`);
            }
        }catch(e){
            alert(`Upload error: ${e}`);
        }
    }

    trackInfo.innerHTML=`🎵 No track selected`;
    loadMusic();
}

async function deleteTrack(name){
    if(!confirm(`Delete "${name}"?`))return;
    try{
        const r=await fetch('/api/music/delete',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({name})
        });
        const d=await r.json();
        if(d.success){
            const deletedWasCurrent =
                currentTrackIndex >= 0 &&
                playlist[currentTrackIndex] &&
                playlist[currentTrackIndex].name === name;

            await loadMusic();

            if(deletedWasCurrent){
                audio.pause();
                audio.src='';
                trackInfo.innerHTML='🎵 No track selected';
                currentTrackIndex=-1;
            }else if(currentTrackIndex >= playlist.length){
                currentTrackIndex = playlist.length - 1;
            }

            renderPlaylist();
        }else{
            alert(d.message);
        }
    }catch(e){alert('Delete error: '+e)}
}

// ─── Drag & Drop ────────────────────────────────────────────
const uploadArea=document.getElementById('upload-area');

uploadArea.addEventListener('dragover',(e)=>{
    e.preventDefault();
    uploadArea.classList.add('dragover');
});

uploadArea.addEventListener('dragleave',()=>{
    uploadArea.classList.remove('dragover');
});

uploadArea.addEventListener('drop',(e)=>{
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    uploadFiles(e.dataTransfer.files);
});

// ─── Init ────────────────────────────────────────────────────
loadStatus();
loadMusic();
setInterval(loadStatus,5000);
</script>

</body>
</html>
"""






@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == WEB_PASSWORD:
            flask_session["logged_in"] = True
            return redirect(url_for("index"))
        else:
            error = "Wrong password"
            time.sleep(1)
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    flask_session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template_string(DASHBOARD_HTML)






@app.route("/api/status")
def api_status():
    if not is_logged_in():
        return jsonify({"error": "Not logged in"}), 401

    if _automation is None:
        return jsonify({
            "mirror_mode": False,
            "total_clones": 0,
            "active_loops": 0,
            "loops": [],
        })

    clones_count = len(_automation.clone_clients)
    loops = [
        {"chat_id": cid, "text": txt}
        for (cid, txt) in _automation.loops.active.keys()
    ]

    return jsonify({
        "mirror_mode": _automation.mirror_mode,
        "total_clones": clones_count,
        "active_loops": len(loops),
        "loops": loops,
    })






@app.route("/api/music/list")
def api_music_list():
    if not is_logged_in():
        return jsonify({"error": "Not logged in"}), 401
    return jsonify({"files": list_music_files()})


@app.route("/api/music/upload", methods=["POST"])
def api_music_upload():
    if not is_logged_in():
        return jsonify({"error": "Not logged in"}), 401

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file provided"})

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "message": "Empty filename"})

    data = file.read()
    result = save_music_file(file.filename, data)
    return jsonify(result)


@app.route("/api/music/delete", methods=["POST"])
def api_music_delete():
    if not is_logged_in():
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": "No name"})

    return jsonify(delete_music_file(name))


@app.route("/api/music/stream/<path:filename>")
def api_music_stream(filename):
    if not is_logged_in():
        return abort(401)

    fpath = get_music_file_path(filename)
    if not fpath:
        return abort(404)

    return send_file(
        fpath,
        mimetype="audio/mp4",
        as_attachment=False,
        conditional=True,
    )






def run_web_server(host="0.0.0.0", port=1500):
    thread = threading.Thread(
        target=lambda: serve(
            app,
            host=host,
            port=port,
        ),
        daemon=True,
        name="WebDashboard",
    )
    thread.start()
    return thread
