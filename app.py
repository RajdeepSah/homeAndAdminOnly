from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector
from datetime import date

app = Flask(__name__)
app.secret_key = "replace-with-a-secure-random-value"

def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="12345",
        database="champions_league"
    )

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/groups/<int:group_id>")
def groups(group_id):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    # 1) load all groups for tabs
    cur.execute("SELECT group_id, name FROM GroupTable ORDER BY name")
    groups = cur.fetchall()

    # 2) name of current group
    cur.execute("SELECT name FROM GroupTable WHERE group_id=%s", (group_id,))
    grp = cur.fetchone()
    current_group_name = grp["name"] if grp else ""

    # 3) standings for this group
    cur.execute("""
      SELECT 
        t.team_id,
        t.name        AS team_name,
        t.logo_url    AS team_logo,
        s.wins,
        s.draw,
        s.loss,
        s.points,
        (s.wins + s.draw + s.loss) AS played,
        s.points >= (
          SELECT MIN(points) FROM Standings WHERE group_id=%s LIMIT 2 OFFSET 1
        ) AS qualified
      FROM Standings s
      JOIN Team t    ON s.team_id = t.team_id
      WHERE s.group_id=%s
      ORDER BY s.points DESC, (s.wins - s.loss) DESC
    """, (group_id, group_id))
    standings = cur.fetchall()

    # 4) team cards (coach + points + position)
    team_cards = []
    for idx, row in enumerate(standings, start=1):
        cur.execute("""
          SELECT m.name AS coach
          FROM Manager m
          WHERE m.team_id=%s
          LIMIT 1
        """, (row["team_id"],))
        coach = cur.fetchone()
        team_cards.append({
            "team_id": row["team_id"],
            "team_name": row["team_name"],
            "logo": row["team_logo"],
            "coach": coach["coach"] if coach else "—",
            "points": row["points"],
            "position": idx
        })

    cur.close()
    conn.close()

    return render_template(
        "group.html",
        groups=groups,
        current_group_id=group_id,
        current_group_name=current_group_name,
        standings=standings,
        team_cards=team_cards
    )

@app.route("/teams")
def teams():
    current_group = request.args.get("group", "")
    search_query  = request.args.get("search", "")

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    sql = """
      SELECT 
        t.team_id,
        t.name     AS team_name,
        t.logo_url AS logo,
        m.name     AS coach,
        gt.group_id,
        g.name     AS group_name,
        s.wins, s.draw, s.loss, s.points
      FROM Team t
      LEFT JOIN Manager m   ON t.team_id = m.team_id
      JOIN GroupTeam gt     ON t.team_id = gt.team_id
      JOIN GroupTable g     ON gt.group_id = g.group_id
      JOIN Standings s      ON t.team_id = s.team_id
                            AND s.group_id = g.group_id
    """
    filters, params = [], []

    if current_group:
        filters.append("g.name=%s")
        params.append(current_group)
    if search_query:
        filters.append("t.name LIKE %s")
        params.append(f"%{search_query}%")

    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY g.name, s.points DESC"

    cur.execute(sql, tuple(params))
    teams = cur.fetchall()
    cur.close()
    conn.close()

    group_list = [chr(ord("A")+i) for i in range(8)]
    return render_template(
        "teams.html",
        teams=teams,
        current_group=current_group,
        search_query=search_query,
        group_list=group_list
    )

@app.route("/players")
def players():
    search     = request.args.get("search", "")
    team_id    = request.args.get("team", "")
    pos_filter = request.args.get("position", "")

    conn = get_db_connection()
    cur  = conn.cursor(dictionary=True)

    cur.execute("SELECT team_id, name FROM Team ORDER BY name")
    teams = cur.fetchall()

    cur.execute("SELECT DISTINCT position FROM Player ORDER BY position")
    positions = [r["position"] for r in cur.fetchall()]

    sql = """
      SELECT
        p.player_id,
        p.name,
        p.jersey_number,
        p.position,
        c.name                AS nationality,
        TIMESTAMPDIFF(YEAR,p.birth_date,CURDATE()) AS age,
        t.team_id,
        t.name                AS team,
        t.logo_url            AS team_logo,
        COALESCE(SUM(ps.goals),0)   AS goals,
        COALESCE(SUM(ps.assists),0) AS assists
      FROM Player p
      JOIN Team t   ON p.team_id = t.team_id
      JOIN Country c ON p.nationality = c.country_id
      LEFT JOIN PlayerMatchStats ps ON p.player_id = ps.player_id
    """
    filters, params = [], []

    if search:
        filters.append("p.name LIKE %s"); params.append(f"%{search}%")
    if team_id:
        filters.append("p.team_id=%s"); params.append(team_id)
    if pos_filter:
        filters.append("p.position=%s"); params.append(pos_filter)

    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " GROUP BY p.player_id ORDER BY goals DESC, assists DESC"

    cur.execute(sql, tuple(params))
    players = cur.fetchall()
    cur.close()
    conn.close()

    return render_template(
      "player.html",
      players=players,
      teams=teams,
      positions=positions,
      search=search,
      team_filter=team_id,
      pos_filter=pos_filter
    )

@app.route("/matches")
def matches():
    search     = request.args.get("search", "").strip()
    team_id    = request.args.get("team", "")
    group_name = request.args.get("group", "")

    conn = get_db_connection()
    cur  = conn.cursor(dictionary=True)

    cur.execute("SELECT team_id, name FROM Team ORDER BY name")
    teams = cur.fetchall()

    cur.execute("SELECT name FROM GroupTable ORDER BY name")
    groups = [r["name"] for r in cur.fetchall()]

    base_sql = """
      SELECT
        m.match_id,
        m.date            AS match_date,
        v.name            AS venue_name,
        v.city            AS venue_city,
        hteam.team_id     AS home_id,
        hteam.name        AS home_team,
        hteam.logo_url    AS home_logo,
        ht.goal_scored    AS home_score,
        ateam.team_id     AS away_id,
        ateam.name        AS away_team,
        ateam.logo_url    AS away_logo,
        at.goal_scored    AS away_score
      FROM Matches m
      JOIN Venue v      ON m.venue_id = v.venue_id
      JOIN MatchTeam ht ON m.match_id = ht.match_id AND ht.is_home = TRUE
      JOIN Team hteam   ON ht.team_id = hteam.team_id
      JOIN MatchTeam at ON m.match_id = at.match_id AND at.is_home = FALSE
      JOIN Team ateam   ON at.team_id = ateam.team_id
    """
    filters, params = [], []

    if search:
        term = f"%{search}%"
        filters.append("(hteam.name LIKE %s OR ateam.name LIKE %s OR v.name LIKE %s OR m.date LIKE %s)")
        params += [term,term,term,term]
    if team_id:
        filters.append("(ht.team_id=%s OR at.team_id=%s)")
        params += [team_id, team_id]
    if group_name:
        filters.append("""
          EXISTS (
            SELECT 1 FROM GroupTeam gt2
            JOIN GroupTable g2 ON gt2.group_id=g2.group_id
            WHERE g2.name=%s AND gt2.team_id IN (ht.team_id,at.team_id)
          )
        """)
        params.append(group_name)

    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""

    cur.execute(f"{base_sql}{where_clause} AND m.date>=CURDATE() ORDER BY m.date", tuple(params))
    upcoming = cur.fetchall()

    cur.execute(f"{base_sql}{where_clause} AND m.date<CURDATE() ORDER BY m.date DESC", tuple(params))
    results  = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
      "matches.html",
      teams=teams,
      groups=groups,
      upcoming_matches=upcoming,
      results_matches=results,
      search=search,
      current_team=team_id,
      current_group=group_name
    )

@app.route("/admin", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        u=request.form["username"]
        p=request.form["password"]
        if u=="admin" and p=="admin123":
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid username or password","danger")
    return render_template("admin.html")

@app.route("/admin/dashboard")
def admin_dashboard():
    return render_template("admin_dashboard.html")



if __name__=="__main__":
    app.run(host="0.0.0.0", debug=True)
