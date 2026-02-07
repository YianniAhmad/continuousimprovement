import os
import secrets
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, abort
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI

from app.database import (
    init_db,
    get_conn,
    placeholder,
    returning_id_clause,
    get_inserted_id,
)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-fallback")

# Create tables if missing + ensure migrations (public_id, etc.)
init_db()


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapper


def generate_public_id() -> str:
    # token_urlsafe(6) is usually ~8-10 chars, URL-safe, good for share links
    return secrets.token_urlsafe(6)


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    p = placeholder()

    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        with get_conn() as conn:
            user = conn.execute(
                f"SELECT * FROM users WHERE email = {p}",
                (email,),
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["email"] = user["email"]
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid email or password.")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    p = placeholder()

    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if not email or not password:
            return render_template("register.html", error="Email and password required.")

        with get_conn() as conn:
            try:
                conn.execute(
                    f"INSERT INTO users (email, password_hash) VALUES ({p}, {p})",
                    (email, generate_password_hash(password)),
                )
                conn.commit()
            except Exception:
                return render_template("register.html", error="Email already registered.")

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    p = placeholder()
    user_id = session["user_id"]

    with get_conn() as conn:
        forms = conn.execute(
            f"SELECT * FROM forms WHERE owner_id = {p} ORDER BY id DESC",
            (user_id,),
        ).fetchall()

    return render_template("dashboard.html", forms=forms)


@app.route("/forms/new", methods=["GET", "POST"])
@login_required
def create_form():
    p = placeholder()

    if request.method == "POST":
        title = request.form["title"].strip()
        description = request.form.get("description", "").strip()
        questions = [q.strip() for q in request.form.getlist("questions[]") if q.strip()]

        if not title:
            return render_template("create_form.html", error="Title is required.")
        if len(questions) < 1:
            return render_template("create_form.html", error="You must add at least 1 question.")

        owner_id = session["user_id"]

        with get_conn() as conn:
            cur = conn.cursor()

            # Generate token and insert. If UNIQUE collision occurs (extremely unlikely),
            # retry a couple times.
            for _ in range(5):
                public_id = generate_public_id()
                try:
                    cur.execute(
                        f"""
                        INSERT INTO forms (public_id, owner_id, title, description)
                        VALUES ({p}, {p}, {p}, {p})
                        {returning_id_clause()}
                        """,
                        (public_id, owner_id, title, description),
                    )
                    form_id = get_inserted_id(cur)
                    break
                except Exception:
                    # likely unique collision (or db error). We'll retry a few times.
                    # If it's a different error, it'll fail again and we'll raise below.
                    continue
            else:
                raise RuntimeError("Failed to create a unique public_id for the form.")

            for idx, q in enumerate(questions, start=1):
                cur.execute(
                    f"INSERT INTO questions (form_id, question_text, position) VALUES ({p}, {p}, {p})",
                    (form_id, q, idx),
                )

            conn.commit()

        return redirect(url_for("dashboard"))

    return render_template("create_form.html")


# PUBLIC share link uses public_id token (unguessable)
@app.route("/forms/<public_id>", methods=["GET", "POST"])
def form_page(public_id: str):
    p = placeholder()

    with get_conn() as conn:
        form = conn.execute(
            f"SELECT * FROM forms WHERE public_id = {p}",
            (public_id,),
        ).fetchone()

        if form is None:
            abort(404)

        form_id = form["id"]

        questions = conn.execute(
            f"SELECT * FROM questions WHERE form_id = {p} ORDER BY position ASC",
            (form_id,),
        ).fetchall()

        if request.method == "POST":
            cur = conn.cursor()
            for q in questions:
                answer = request.form.get(f"q_{q['id']}", "").strip()
                if answer:
                    cur.execute(
                        f"INSERT INTO answers (form_id, question_id, answer_text) VALUES ({p}, {p}, {p})",
                        (form_id, q["id"], answer),
                    )
            conn.commit()
            return render_template("thank_you.html", form=form)

    return render_template("form.html", form=form, questions=questions)


# AUTH routes remain int form_id (fine)
@app.route("/dashboard/forms/<int:form_id>/results")
@login_required
def form_results(form_id: int):
    p = placeholder()
    user_id = session["user_id"]

    with get_conn() as conn:
        form = conn.execute(
            f"SELECT * FROM forms WHERE id = {p} AND owner_id = {p}",
            (form_id, user_id),
        ).fetchone()

        if form is None:
            return "Not found", 404

        answers = conn.execute(
            f"""
            SELECT q.question_text, a.answer_text, a.created_at
            FROM answers a
            JOIN questions q ON q.id = a.question_id
            WHERE a.form_id = {p}
            ORDER BY a.created_at DESC
            """,
            (form_id,),
        ).fetchall()

        summary_row = conn.execute(
            f"""
            SELECT summary_text
            FROM ai_summaries
            WHERE form_id = {p}
            ORDER BY id DESC
            LIMIT 1
            """,
            (form_id,),
        ).fetchone()

    return render_template(
        "form_results.html",
        form=form,
        answers=answers,
        summary=summary_row["summary_text"] if summary_row else None,
    )


@app.route("/dashboard/forms/<int:form_id>/summary", methods=["POST"])
@login_required
def generate_summary(form_id: int):
    p = placeholder()
    user_id = session["user_id"]

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "OPENAI_API_KEY not set on server", 500

    client = OpenAI(api_key=api_key)

    with get_conn() as conn:
        form = conn.execute(
            f"SELECT * FROM forms WHERE id = {p} AND owner_id = {p}",
            (form_id, user_id),
        ).fetchone()

        if form is None:
            return "Not found", 404

        rows = conn.execute(
            f"""
            SELECT q.position, q.question_text, a.answer_text, a.created_at
            FROM answers a
            JOIN questions q ON q.id = a.question_id
            WHERE a.form_id = {p}
            ORDER BY q.position ASC, a.created_at DESC
            """,
            (form_id,),
        ).fetchall()

    qa_lines = []
    for r in rows:
        qa_lines.append(f"Q{r['position']}: {r['question_text']}\n- {r['answer_text']}")
    qa_text = "\n\n".join(qa_lines) if qa_lines else "No answers yet."

    prompt = f"""
You are an operations style feedback analyst for a team.

Given the following anonymous feedback answers, produce:
1) Top 5 recurring themes (ranked, with brief evidence)
2) Top 5 most common concrete suggestions (ranked)
3) Actionable next steps (7-day plan + 30-day plan)
4) Risks / red flags (if any)
5) A short executive summary (5 bullet points)

Rules:
- Be specific and practical.
- Don’t invent facts.
- If data is thin, say so.
- Within your reply, NEVER use boldings ** or * as it does not format properly. Keep it to plain normal text only.

FORM TITLE: {form['title']}
FORM DESCRIPTION: {form['description']}

FEEDBACK (Q&A):
{qa_text}
""".strip()

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    summary_text = resp.output_text

    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO ai_summaries (form_id, summary_text) VALUES ({p}, {p})",
            (form_id, summary_text),
        )
        conn.commit()

    return redirect(url_for("form_results", form_id=form_id))


@app.route("/dashboard/forms/<int:form_id>/delete", methods=["POST"])
@login_required
def delete_form(form_id: int):
    p = placeholder()
    user_id = session["user_id"]

    with get_conn() as conn:
        form = conn.execute(
            f"SELECT id FROM forms WHERE id = {p} AND owner_id = {p}",
            (form_id, user_id),
        ).fetchone()

        if form is None:
            return "Not found", 404

        conn.execute(
            f"DELETE FROM forms WHERE id = {p} AND owner_id = {p}",
            (form_id, user_id),
        )
        conn.commit()

    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True, port=9292)
