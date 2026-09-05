"""Local administrator initialization, never a public registration endpoint."""
import argparse
import getpass
from uuid import uuid4
from .app import PASSWORDS, Settings
from .models import Login
from .storage import Store, now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create-admin", "reset-password"])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("管理员密码（至少 14 个字符）: ")
    if len(password) < 14 or password != getpass.getpass("再次输入: "):
        raise SystemExit("密码过短或两次输入不一致")
    data = Login(email=args.email, password=password)
    store = Store(Settings.environment().database)
    hashed = PASSWORDS.hash(data.password)
    with store.connection(write=True) as c:
        existing = c.execute("SELECT id FROM admins WHERE email=?", (data.email,)).fetchone()
        if args.action == "create-admin":
            if existing:
                raise SystemExit("管理员已存在，请使用 reset-password")
            c.execute("INSERT INTO admins VALUES(?,?,?,?)", (str(uuid4()), data.email, hashed, now()))
        else:
            if not existing:
                raise SystemExit("管理员不存在")
            c.execute("UPDATE admins SET password_hash=? WHERE id=?", (hashed, existing[0]))
            c.execute("DELETE FROM sessions WHERE admin_id=?", (existing[0],))
            store.audit(c, existing[0], "reset_password", None, None, None, "服务器本地重置密码并撤销会话")
    print("管理员凭据已安全保存；密码不会输出。")


if __name__ == "__main__":
    main()
