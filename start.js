module.exports = {
    daemon: true,
    run: [
        // Clean app-level Python bytecode cache
        {
            method: "fs.rm",
            params: {
                path: "__pycache__"
            }
        },
        {
            method: "fs.rm",
            params: {
                path: "modules/__pycache__"
            }
        },
        // Launch app with no bytecode caching
        {
            method: "shell.run",
            params: {
                venv: "env",
                path: ".",
                env: {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1"
                },
                message: [
                    "python app.py",
                ],
                on: [{
                    "event": "/(http:\\/\\/[0-9.:]+)/",
                    "done": true
                }]
            }
        },
        {
            method: "local.set",
            params: {
                url: "{{input.event[1]}}"
            }
        },
    ]
}
