// The project CI container is started explicitly rather than through
// `agent { docker }` because each worker's Docker socket has its own GID, which
// is resolved at runtime instead of assumed.
def dockerArgs() {
    def socketGid = sh(script: 'stat -c %g /var/run/docker.sock', returnStdout: true).trim()
    return "--group-add ${socketGid} " +
        '-v /var/run/docker.sock:/var/run/docker.sock ' +
        '-v /usr/bin/docker:/usr/bin/docker ' +
        '-v /usr/libexec/docker:/usr/libexec/docker'
}

// Run a block inside this build's image. The numbered tag is the immutable run
// input, so every stage after Setup sees the same image.
def inCiImage(Closure body) {
    docker.image("${env.CI_IMAGE}:${env.BUILD_NUMBER}").inside(dockerArgs(), body)
}

pipeline {
    // Every Jenkins worker has the `docker` label.
    agent { label 'docker' }

    options {
        ansiColor('xterm')
        timeout(time: 180, unit: 'MINUTES')
    }

    environment {
        CI = 'true'
        HOME = "${WORKSPACE}"
        CI_IMAGE = 'ci-trl-gauntlet'
        APT_PROXY_URL = 'http://192.168.11.112:3142'
    }

    stages {
        stage('Setup') {
            steps {
                script {
                    docker.build(
                        "${CI_IMAGE}:cache",
                        "--build-arg APT_PROXY_URL=${APT_PROXY_URL} --file ci/Dockerfile .",
                    )
                    // Keep a stable local tag so a worker retains BuildKit layers across
                    // build numbers; the numbered tag remains the immutable run input.
                    sh "docker tag ${CI_IMAGE}:cache ${CI_IMAGE}:${BUILD_NUMBER}"
                    sh 'mkdir -p .ci-cache/{uv,npm,electron,electron-builder}'
                    inCiImage {
                        // Start the SSH agent inside the CI container so its UNIX socket
                        // is usable for private submodule cloning.
                        sshagent(credentials: ['github-ssh']) {
                            sh 'git submodule update --init --recursive --depth 1'
                            sh 'make setup'
                        }
                    }
                }
            }
        }
        stage('Verify') {
            steps {
                script {
                    inCiImage {
                        sh 'make check'
                    }
                }
            }
        }
        stage('Build') {
            steps {
                script {
                    inCiImage {
                        sh 'make build'
                        sh 'make ci-validate-dist'
                    }
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'dist/*', allowEmptyArchive: true, fingerprint: true
            junit allowEmptyResults: true, testResults: 'build/junit.xml'
            cleanWs(deleteDirs: true, patterns: [
                [pattern: '.ci-cache/**', type: 'EXCLUDE'],
                [pattern: '.git/**', type: 'EXCLUDE'],
            ])
        }
    }
}
