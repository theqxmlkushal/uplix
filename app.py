"""Flask app for the customer shop (Member A, README section 5).

Vercel finds `app` in this file with no extra configuration. The customer routes live in the
`shop` blueprint (shop.py); this file only wires it up, keeps the /health check from the starter,
and turns errors into a friendly page.
"""
import os

from dotenv import load_dotenv

load_dotenv()                              # local .env only; never overrides a var already set
                                            # (tests set DATABASE_URL themselves first, so they stay in charge)

from flask import Flask, jsonify, render_template, send_from_directory

from admin import admin
from db import close_connection, query
from shop import shop

app = Flask(__name__, static_folder=None)           # on Vercel, static files live in public/
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.register_blueprint(shop)
app.register_blueprint(admin)
app.teardown_appcontext(close_connection)              # one connection per request, closed here


@app.route("/health")
def health():
    """Counts per table: a quick way to check that Vercel can reach Supabase."""
    counts = {}
    for table in ("participants", "products", "events", "orders", "assignments"):
        counts[table] = query(f"select count(*) as n from {table}", one=True)["n"]
    return jsonify(status="ok", counts=counts, state=query("select state from app_state", one=True)["state"])


@app.route("/shop.css")
def shop_css():
    """Serve the stylesheet when running locally; on Vercel the public/ folder is served for us."""
    return send_from_directory(os.path.join(app.root_path, "public"), "shop.css")


@app.route("/admin.css")
def admin_css():
    """Same arrangement for the console's own stylesheet."""
    return send_from_directory(os.path.join(app.root_path, "public"), "admin.css")


@app.route("/admin.js")
def admin_js():
    """The console's chart and interaction code (plain vanilla, no CDN)."""
    return send_from_directory(os.path.join(app.root_path, "public"), "admin.js")


def error_page(message, code):
    """One friendly page for every error (README 5.4: a friendly page for errors)."""
    return render_template("shop/error.html", message=message), code


@app.errorhandler(404)
def not_found(_error):
    return error_page("That page does not exist. Try the catalogue instead.", 404)


@app.errorhandler(403)
def forbidden(_error):
    return error_page("You cannot do that right now.", 403)


@app.errorhandler(500)
def crashed(_error):
    return error_page("Something went wrong on our side. Please go back and try again.", 500)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
