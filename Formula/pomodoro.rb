class Pomodoro < Formula
  desc "Terminal-based Pomodoro timer with statistics and CalDAV sync"
  homepage "https://github.com/JacobOmateq/pomodoro"
  url "https://github.com/JacobOmateq/pomodoro/archive/refs/heads/main.zip"
  version "1.0.0"
  sha256 "" # This will be calculated when you create a release
  
  depends_on "python@3.11"

  def install
    python3 = Formula["python@3.11"].opt_bin/"python3.11"
    venv = virtualenv_create(libexec, python3)
    
    # Install dependencies from requirements.txt
    requirements = buildpath/"requirements.txt"
    venv.pip_install requirements
    
    # Install the script
    bin.install "pomodoro.py" => "pomodoro"
    chmod 0755, bin/"pomodoro"
    
    # Create a wrapper script that uses the virtualenv
    bin.env_script_all_files(libexec/"bin", :PATH => "#{libexec}/bin:$PATH")
  end

  test do
    system "#{bin}/pomodoro", "--help"
  end
end
