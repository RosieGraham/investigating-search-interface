from django.contrib.auth.models import AbstractUser, UserManager
from django.db.models.functions import Upper


class CustomUserManager(UserManager):
    def get_by_natural_key(self, username):
        """
        Allow users to login with case-insensitive username

        E.g. both "My.Name@uni.ac.uk" and "my.name@uni.ac.uk" will allow users to login
        """
        return self.get(username__iexact=username)


class User(AbstractUser):
    """
    Custom user extends the standard Django user model, providing additional properties
    """

    # Custom user manager used to allow for case-insensitive usernames
    objects = CustomUserManager()

    @property
    def name(self):
        if self.first_name and self.last_name:
            return ' '.join((self.first_name, self.last_name))
        elif self.first_name:
            return self.first_name
        elif self.last_name:
            return self.last_name
        else:
            # If no first or last name provided, return first half of email
            return self.username.split('@')[0]  # e.g. mike.allaway in mike.allaway@ox.ac.uk

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Force email and username to be lower case and identical, so users can login with email.
        # This rewrite is LOAD-BEARING: every login flow and ensure_superuser depend on it.
        if self.email:
            self.email = self.email.strip().lower()
            self.username = self.email
        # NOTE (July 2026): this method used to force is_staff and is_superuser True on
        # every save (inherited from the old bham system so accounts could reach the
        # dashboard). Removed so non-privileged accounts can exist: staff editors get
        # is_staff=True only, and participant-data tables stay superuser-only. Privilege
        # flags are now set explicitly where accounts are created (admin, ensure_superuser).
        # Apply changes to user object
        super().save(*args, **kwargs)

    class Meta:
        ordering = [Upper('first_name'), Upper('last_name'), Upper('email'), 'id']
