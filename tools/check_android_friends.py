"""Use two independent Android installs to verify Zompado friends and turns."""

import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import cv2
import numpy as np

APP = "io.tablecircle.prototype"
ACTIVITY = APP + "/.MainActivity"


def wait(label, predicate, seconds=55):
    expires = time.monotonic() + seconds
    while time.monotonic() < expires:
        result = predicate()
        if result:
            return result
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {label}")


class Phone:
    def __init__(self, port, name):
        self.serial = f"emulator-{port}"
        self.name = name

    def adb(self, *args):
        result = subprocess.run(
            [sys.argv[1], "-s", self.serial, *map(str, args)],
            capture_output=True, timeout=25,
        )
        if result.returncode:
            raise RuntimeError(f"{self.name}: adb {args[0]} failed")
        return result.stdout

    def shell(self, *args):
        return self.adb("shell", *args)

    def image(self):
        data = self.adb("exec-out", "screencap", "-p")
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)

    def find(self, label):
        try:
            self.shell("uiautomator", "dump", "/sdcard/zompado-friends.xml")
            layout = self.shell("cat", "/sdcard/zompado-friends.xml")
            editable = []
            for node in ET.fromstring(layout).iter("node"):
                x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds", "")))
                if x2 <= x1 or y2 <= y1:
                    continue
                center = ((x1 + x2) // 2, (y1 + y2) // 2)
                if label in (node.get("text", "") + "\n" + node.get("content-desc", "")).splitlines():
                    return center
                if node.get("class") == "android.widget.EditText":
                    editable.append(center)
            if label == "<input>" and len(editable) == 1:
                return editable[0]
        except (RuntimeError, ET.ParseError):
            return None
        return None

    def labels(self):
        self.shell("uiautomator", "dump", "/sdcard/zompado-friends.xml")
        layout = self.shell("cat", "/sdcard/zompado-friends.xml")
        values = [node.get("content-desc") or node.get("text") for node in ET.fromstring(layout).iter("node")]
        return [label for label in values if label and len(label) <= 60]

    def see(self, label, seconds=55):
        return wait(f"{self.name} to show {label}", lambda: self.find(label), seconds)

    def press(self, label, seconds=55):
        self.shell("input", "tap", *self.see(label, seconds))

    def type(self, text):
        self.press("<input>")
        self.shell("input text " + shlex.quote(text.replace(" ", "%s")))

    def back(self):
        self.shell("input", "keyevent", 4)

    def start(self):
        result = self.shell("am", "start", "-W", "-n", ACTIVITY).decode()
        if "Error" in result or "Exception" in result:
            raise RuntimeError(f"{self.name}: could not launch Zompado: {result}")

    def stop_background_process(self):
        self.shell("input", "keyevent", 3)
        def process():
            return self.shell(f"pidof {APP} || true").decode().split()
        for pid in process():
            if pid.isdecimal():
                # Run under the debug app's UID so this also works if LMK considers it active.
                self.shell("run-as", APP, "kill", "-9", pid)
        wait(f"{self.name}'s process to stop without force-stop", lambda: not process())

    def install_game(self):
        self.press("Get Games")
        self.see("Cats vs Dogs", 100)
        self.press("Install", 100)
        self.see("Play", 100)

    def profile(self):
        self.shell("pm", "grant", APP, "android.permission.POST_NOTIFICATIONS")
        self.shell("pm", "grant", APP, "android.permission.RECORD_AUDIO")
        self.start()
        self.see("Welcome to Zompado", 85)
        if not self.find("Finish setup"):
            self.shell("input", "swipe", 350, 1100, 350, 370, 400)
        self.press("Finish setup")
        self.press("Chats")
        self.type(self.name)
        self.back()
        self.press("Create chat profile")
        self.see("Inbox", 120)

    def invite(self):
        self.press("Contacts")
        self.press("Create Invite")
        self.see("Create invite")
        detector = cv2.QRCodeDetector()
        link = wait("a decodable invite QR", lambda: detector.detectAndDecode(self.image())[0], 30)
        self.press("Done")
        self.back()
        return link

    def turn(self):
        def board_center():
            image = self.image()
            if image is None:
                return None
            height, width = image.shape[:2]
            x = width // 2
            blue = np.array([125, 69, 38])
            rows = np.where(np.max(np.abs(image[:, x].astype(int) - blue), axis=1) <= 3)[0]
            if len(rows) > 30 and rows[-1] - rows[0] > width // 2:
                return x, int(rows[len(rows) // 2])
            return None
        self.shell("input", "tap", *wait(f"{self.name} online board", board_center, 75))


def check_voice(alice, bob):
    bob.press("<input>")
    alice.press("Voice call")
    alice.see("Call")
    alice.back()
    alice.see("Voice call")
    if bob.find("Incoming call from Alice"):
        raise RuntimeError("Opening the dialer started a call before confirmation")
    alice.press("Voice call")
    alice.press("Call")
    bob.see("Incoming call from Alice", 75)
    alice.see("Calling Bob…", 20)
    bob.press("Answer")
    alice.see("Voice call with Bob", 75)
    bob.see("Voice call with Alice", 75)
    bob.press("Mute microphone")
    bob.press("Unmute microphone")
    bob.press("Turn on speaker")
    bob.press("Turn off speaker")
    bob.press("Switch to video")
    bob.see("Video call with Alice", 30)
    alice.see("Video call with Bob", 30)
    wait("Alice to render Bob’s camera", lambda:
         b"Remote camera frame rendered" in alice.adb("logcat", "-d", "-s", "ZompadoCall:I"), 30)
    bob.press("Hide preview")
    alice.see("Video call with Bob")
    bob.press("Show preview")
    alice.press("Switch to audio")
    alice.see("Voice call with Bob", 30)
    bob.see("Video call with Alice")
    alice.press("Switch to video")
    alice.see("Video call with Bob", 30)
    bob.press("Switch to audio")
    alice.see("Voice call with Bob", 30)
    bob.press("End call")
    wait("remote call closure", lambda: not alice.find("End call"))
    print("PASS: two Android WebRTC endpoints connected, received video from an enabled camera, toggled mute and speaker, and cleared remote hangup.", flush=True)


def check_message_tools(alice, bob):
    alice.type("Saved chat check")
    alice.back()
    alice.press("Send message")
    bob.see("Saved chat check", 100)
    bob.press("Save message")
    bob.see("Unsave message")
    bob.press("Saved")
    bob.see("Photos")
    bob.see("Saved chat check")
    bob.back()
    bob.press("React to message")
    bob.press("grinning face happy smile grin")
    bob.press("Done")
    alice.see("😀", 100)
    bob.press("Mute all notifications")
    bob.see("Unmute all notifications")
    bob.press("Unmute all notifications")
    bob.see("Mute all notifications")
    print("PASS: saved an incoming message, opened Saved, reacted across devices, and toggled the global bell.", flush=True)


def check_match(alice, bob):
    alice.press("Invite to play a game")
    alice.see("Choose a game to play with Bob")
    alice.press("Cats vs Dogs")
    alice.press("Invite")
    alice.see("Waiting for your friend to accept.", 100)
    alice.back()
    alice.back()
    alice.press("Games")
    alice.press("Play")
    alice.press("Resume an Active Game")
    alice.see("Cats vs Dogs with Bob")
    alice.press("Open game")
    alice.see("Waiting for your friend to accept.", 100)

    bob.press("Accept", 100)
    bob.see("Waiting for Alice to start the game.", 100)
    alice.press("Play Now", 100)
    bob.see("Their Turn", 100)
    alice.see("Your Turn", 100)
    alice.back()
    alice.press("Resume Game")
    alice.see("Your Turn", 100)
    print("PASS: Resume Game opened the board directly.", flush=True)
    alice.press("Voice call")
    bob.see("Incoming call from Alice", 75)
    bob.press("Answer")
    alice.see("Voice call with Bob", 75)
    bob.see("Voice call with Alice", 75)
    alice.press("Return to game")
    alice.see("Your Turn")
    bob.see("Voice call with Alice")
    alice.press("Open call")
    alice.see("Voice call with Bob")
    alice.press("End call")
    alice.see("Your Turn")
    bob.see("Their Turn")
    print("PASS: a call started from the match, stayed connected on the board, and reopened from the game toolbar.", flush=True)
    bob.press("Open match chat")
    # Wait for the match composer keyboard to take focus before injecting text.
    bob.press("<input>")
    wait("Bob’s keyboard", lambda: b"mInputShown=true" in bob.shell("dumpsys", "input_method"))
    bob.shell("input", "text", "game%schat%scheck")
    bob.back()
    bob.press("Send message")
    alice.see("Bob: game chat check", 90)
    bob.press("Close chat panel")
    print("PASS: the game chat opened, sent a message preview, and closed from its handle.", flush=True)
    alice.press("Pause")
    bob.see("Alice paused the game.", 100)
    alice.press("Resume")
    bob.see("Their Turn", 100)
    bob.turn()
    bob.see("Their Turn")
    alice.see("Your Turn", 100)
    bob.stop_background_process()
    alice.turn()
    alice.see("Their Turn")
    bob.shell("cmd", "jobscheduler", "run", "-f", APP, 291)
    bob.shell("cmd", "statusbar", "expand-notifications")
    bob.press("New friend activity", 75)
    bob.see("Your Turn", 75)
    bob.turn()
    bob.see("Their Turn")
    alice.see("Your Turn", 100)
    bob.press("Pause")
    bob.press("Quit game")
    bob.press("Quit match")
    alice.see("Match ended.", 100)
    print("PASS: the online board opened on both phones after acceptance. The inviter took the first turn and the friend replied.", flush=True)
    print("PASS: an Android background job recovered a move, its alert opened the exact board, and the reply returned.", flush=True)


def check_app_log(phone):
    prior = phone.shell("run-as", APP, "cat", "files/app-log.txt")
    startup = next((line for line in prior.splitlines() if b"App process started" in line), None)
    if startup is None:
        raise RuntimeError("App Log did not record startup")
    phone.shell("am", "force-stop", APP)
    wait("the app process to exit", lambda: not phone.shell(f"pidof {APP} || true").strip())
    phone.start()
    phone.press("Settings")
    for _ in range(3):
        if phone.find("App Log"):
            break
        phone.shell("input", "swipe", 350, 1060, 350, 380, 350)
    phone.press("App Log")
    phone.see("Copy Log")
    log = phone.shell("run-as", APP, "cat", "files/app-log.txt")
    if startup not in log or log.count(b"App process started") < 2:
        raise RuntimeError("App Log did not keep the first startup after reopening Zompado")
    phone.press("Clear Log")
    wait("App Log to clear", lambda: startup not in phone.shell(
        f"run-as {APP} cat files/app-log.txt 2>/dev/null || true"
    ))
    phone.press("Copy Log")
    # Android shows a clipboard overlay above the bottom of the app.
    time.sleep(8)
    phone.back()
    phone.back()
    phone.press("Chats")
    print("PASS: App Log kept activity across a restart and its Copy Log and Clear Log controls opened and ran.", flush=True)


def main():
    alice, bob = Phone(5554, "Alice"), Phone(5556, "Bob")
    alice.profile()
    check_app_log(alice)
    bob.profile()
    bob.press("Contacts")
    bob.press("Accept Invite")
    bob.press("Scan QR code")
    bob.shell("input", "tap", *wait("the camera permission denial button",
        lambda: bob.find("Don’t allow") or bob.find("Don't allow")))
    bob.see("Open camera settings")
    bob.press("Scan QR code")
    bob.press("While using the app")
    bob.see("Point the camera at your friend’s invite QR code", 40)
    bob.back()
    bob.see("Scan QR code")
    print("PASS: Android requested camera access, handled denial, and opened the built-in QR scanner.", flush=True)

    link = alice.invite()
    bob.type(link)
    bob.back()
    bob.press("Accept")
    alice.press("Bob", 120)
    bob.see("Alice", 120)
    print("PASS: paired two independent Android installs using the app’s friend invite.", flush=True)

    check_message_tools(alice, bob)

    # Run the two independent checks even if voice media fails on the emulators.
    voice_error = None
    try:
        check_voice(alice, bob)
    except RuntimeError as error:
        voice_error = error
        for phone in (alice, bob):
            print(f"{phone.name} while waiting for the call:", phone.labels(), flush=True)
            logs = phone.adb("logcat", "-d", "-t", "800", "-s", "flutter:E", "ZompadoCall:I").decode(errors="replace")
            if logs.strip():
                print(logs, flush=True)
            for action in ("End call", "Decline", "Dismiss call message"):
                if point := phone.find(action):
                    phone.shell("input", "tap", *point)
        print(f"Voice check failed: {error}", flush=True)

    alice.back()
    alice.press("Games")
    alice.install_game()
    alice.press("Chats")
    alice.press("Bob")
    check_match(alice, bob)
    if voice_error:
        raise voice_error
    log = alice.shell("run-as", APP, "cat", "files/app-log.txt")
    if b"Outgoing phone button pressed" not in log or b"WebRTC native initialization finished" not in log:
        raise RuntimeError("App Log did not record the call startup")
    print("PASS: App Log captured the outgoing tap and native WebRTC startup.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError):
        for port in (5554, 5556):
            phone = Phone(port, f"emulator-{port}")
            try:
                print(f"{phone.name} focus:", flush=True)
                print(phone.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' || true").decode())
                print(phone.adb("logcat", "-b", "crash", "-d", "-t", "120").decode())
                print("Visible labels:", phone.labels())
            except (RuntimeError, subprocess.SubprocessError, ET.ParseError):
                pass
        raise
