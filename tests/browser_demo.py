"""Real Chromium walkthrough; the organiser demo makes no external requests.

NODE_PATH must point to an installation containing playwright. Run this file
from the repository root with the same Python environment as the application.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request


def walkthrough(mode, script):
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='organiser-browser-') as temp:
        env = dict(os.environ, PYTHONPATH=str(repo))
        env['DEMO_SCREENSHOT_DIR'] = os.getenv('DEMO_SCREENSHOT_DIR', temp)
        with open(Path(temp)/'server.log', 'w+') as log:
            process = subprocess.Popen([sys.executable, '-m', 'monitoring.run', '--' + mode], cwd=temp,
                                       env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                ready = False
                for _ in range(120):
                    if process.poll() is not None:
                        raise RuntimeError('Demo server exited before startup.')
                    try:
                        urllib.request.urlopen('http://127.0.0.1:8000', timeout=1).close()
                        ready = True
                        break
                    except OSError:
                        time.sleep(.25)
                if not ready:
                    raise RuntimeError('Demo server did not become ready.')
                result = subprocess.run(['node', str(repo/'tests'/script)], env=env, timeout=180)
                if result.returncode:
                    raise RuntimeError('Chromium organiser walkthrough failed.')
            except Exception:
                log.flush()
                print((Path(temp)/'server.log').read_text()[-5000:])
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    walkthrough('demo', 'browser_demo.cjs')
    walkthrough('demo', 'browser_upload.cjs')
    walkthrough('pilot', 'browser_pilot.cjs')


if __name__ == '__main__':
    main()
