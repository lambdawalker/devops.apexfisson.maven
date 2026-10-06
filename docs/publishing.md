# Publishing and consuming artifacts

Use the HTTPS hostname configured in [setup](setup.md). This repository does
not modify your existing libraries or their Maven Central publishing workflows.
Both destinations can coexist; this URL is a separate repository.

## Scoped publishing tokens

Log in with `admin`, then create a dedicated publisher in the dashboard Console:

```text
token-generate github-permissions
route-add github-permissions /releases/com/apexfission/android/permission/ rw
route-add github-permissions /snapshots/com/apexfission/android/permission/ rw
```

Save the returned token secret; the token name is the HTTP Basic username.
These example routes match the `com.apexfission.android.permission` group.
Use a separate token and appropriate group path for each library. The trailing
slash anchors the prefix at a path boundary. Do not give CI the `m` manager
permission. Read/write allows Maven metadata reads as well as uploads.

For each publishing repository, configure GitHub Actions secrets:

| Secret | Value |
| --- | --- |
| `REPOSILITE_USERNAME` | Token name, e.g. `github-permissions` |
| `REPOSILITE_PASSWORD` | Generated token secret |

Use `token-revoke github-permissions` to revoke access, or generate a replacement
token, update CI, verify it works, then revoke the old token. Kubernetes Secrets
and Reposilite publishing tokens are different credential stores.

## Gradle Kotlin DSL: publish existing Maven publications

Add this in a module that already applies `maven-publish` and defines its Android
release publication (for example through your existing publishing plugin):

```kotlin
publishing {
    repositories {
        maven {
            name = "Reposilite"
            val channel = if (project.version.toString().endsWith("-SNAPSHOT")) {
                "snapshots"
            } else {
                "releases"
            }
            url = uri("https://maven.your-domain.com/$channel")
            credentials {
                username = providers.environmentVariable("REPOSILITE_USERNAME").orNull
                password = providers.environmentVariable("REPOSILITE_PASSWORD").orNull
            }
        }
    }
}
```

Set the final project/publication version before this block. It adds a destination;
it does not create Android publications, apply signing or change coordinates.
Use existing publication/signing setup. Inspect tasks first:

```bash
./gradlew :core:tasks --group publishing
./gradlew :core:publishAllPublicationsToReposiliteRepository
```

Replace `:core` with the actual module. The destination-specific task avoids
accidentally invoking every configured publishing destination. A workflow step:

```yaml
- name: Publish to Reposilite
  env:
    REPOSILITE_USERNAME: ${{ secrets.REPOSILITE_USERNAME }}
    REPOSILITE_PASSWORD: ${{ secrets.REPOSILITE_PASSWORD }}
  run: ./gradlew :core:publishAllPublicationsToReposiliteRepository
```

The workflow still needs checkout, JDK/Android setup and existing signing inputs.
Use separate jobs/actions for modules with independent release lifecycles.

## Gradle Kotlin DSL: consume

In the application's `settings.gradle.kts`:

```kotlin
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
        maven {
            url = uri("https://maven.your-domain.com/releases")
            content {
                includeGroupByRegex("com\\.apexfission\\..*")
            }
        }
    }
}
```

Add `/snapshots` separately only if needed. Use the library's published
coordinates in dependencies. Public repositories need no download token;
private repositories require credentials and a token with `r` route access.
Avoid adding a publisher's write token to consumer applications.

## First publication checks

Publish a disposable test version in an authorized group, download its POM and
AAR/JAR via HTTPS, and resolve it from a clean Gradle consumer. Restart the pod
and repeat the download to verify persistence. Verify an unauthenticated PUT
is rejected and the CI token cannot write outside its configured routes.

`401`/`403`: verify token name, secret and route. `409`: release redeployment is
disabled; publish a new version instead. Connection timeouts: check pod health,
DNS/TLS and load balancer limits before retrying large uploads.
