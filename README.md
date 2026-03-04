# Feature Flags Service

A simple Feature Flags backend built with Django and Docker.

## Tech Stack

* Django
* PostgreSQL
* Redis
* Celery
* Docker

## Run the Project

```bash
docker compose up --build
```

Apply migrations:

```bash
docker compose exec web python manage.py migrate
```

Open in browser:

```
http://localhost:8000
```

---

🚧 This project is still in progress.
